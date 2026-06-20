# apps/worker/ai/detection/segment_dispatcher.py
from __future__ import annotations

import contextvars
import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import cv2  # type: ignore
import numpy as np

from academy.adapters.ai.config import AIConfig
from academy.adapters.ai.detection.segment_opencv import (
    segment_questions_opencv,
    segment_questions_scan_layout,
)
from academy.adapters.ai.detection.segment_yolo import segment_questions_yolo
from academy.adapters.ai.detection.segment_ocr import (
    is_ocr_available,
    segment_questions_ocr,
)

# PDF 200 DPI 렌더링 기준 좌표 변환 (points → pixels)
_PDF_TO_PIXEL_SCALE = 200.0 / 72.0

logger = logging.getLogger(__name__)

BBox = Tuple[int, int, int, int]
_SCAN_LAYOUT_BOXES_DEFAULT = "_scan_layout_boxes_default"
_SCAN_LAYOUT_BOXES_FRAGMENT_MERGED = "_scan_layout_boxes_fragment_merged"
_SCAN_LAYOUT_USE_FRAGMENT_MERGE = "_scan_layout_use_fragment_merge"


def _add_region_semantic_flag(region: object, flag: str) -> None:
    flags = set(getattr(region, "semantic_flags", ()) or ())
    if flag in flags:
        return
    flags.add(flag)
    try:
        setattr(region, "semantic_flags", tuple(sorted(flags)))
    except Exception:
        return


def _bbox_points_to_pixels(
    bbox: Tuple[float, float, float, float],
    *,
    scale: float = _PDF_TO_PIXEL_SCALE,
) -> BBox:
    x0, y0, x1, y1 = bbox
    return (
        int(x0 * scale),
        int(y0 * scale),
        int((x1 - x0) * scale),
        int((y1 - y0) * scale),
    )


def _region_bbox_meta(region: object) -> Dict[str, object]:
    """Serialize v2 region boxes without changing the legacy ``boxes`` contract."""
    display_bbox = getattr(region, "display_bbox", None) or getattr(region, "bbox")
    audit_bbox = getattr(region, "audit_bbox", None) or display_bbox
    body_bbox = getattr(region, "body_bbox", None) or audit_bbox
    context_bbox = getattr(region, "context_bbox", None)
    return {
        "version": "question_region_v2",
        "display_box": _bbox_points_to_pixels(display_bbox),
        "audit_box": _bbox_points_to_pixels(audit_bbox),
        "body_box": _bbox_points_to_pixels(body_bbox),
        "context_box": (
            _bbox_points_to_pixels(context_bbox)
            if context_bbox is not None
            else None
        ),
        "semantic_flags": list(getattr(region, "semantic_flags", ()) or ()),
    }


def _expand_single_text_regions_to_visual_content(
    image_path: str,
    regions: List,
    *,
    page_width: float,
    page_height: float,
) -> None:
    """단일열 text-PDF crop의 x축을 렌더 이미지의 실제 잉크 범위까지 보강한다.

    PyMuPDF text block은 그림/표/벡터 객체를 포함하지 않아, 실제 전폭 문제도
    텍스트 폭만큼 반쪽 crop이 되는 경우가 있다. born-digital 단일열 페이지만
    대상으로 삼고 y축은 splitter 결과를 유지해 다음 문항 침범을 막는다.
    """
    if not regions or page_width <= 0 or page_height <= 0:
        return
    if _regions_already_span_columns(regions, page_width=page_width):
        return
    gray = cv2.imread(image_path, cv2.IMREAD_GRAYSCALE)
    if gray is None or gray.size == 0:
        return

    img_h, img_w = gray.shape[:2]
    scale_x = img_w / page_width
    scale_y = img_h / page_height
    # White PDF background 기준. Anti-aliased text/vector까지 포함하되 연한 배경은 제외.
    ink = gray < 245
    # Page frames are frequent in T2 workbook PDFs. If left/right border lines
    # stay in the ink map, a short left-column text problem can look like it
    # spans the whole page. Strip only the outer frame band; real content boxes
    # begin further inside and are still visible to the expansion heuristic.
    frame_band = max(2, int(img_w * 0.025))
    ink[:, :frame_band] = False
    ink[:, img_w - frame_band:] = False
    pad = page_width * 0.012

    def _has_wide_non_header_ink(roi: np.ndarray) -> bool:
        if roi.size == 0 or roi.shape[0] < 12:
            return False
        # Ignore the top strip of a region: page header rules and subject
        # labels often overlap the first few rendered rows after tightening.
        top_guard = max(8, int(roi.shape[0] * 0.08))
        body_roi = roi[top_guard:, :]
        if body_roi.size == 0:
            return False
        min_row_ink = max(16, int(img_w * 0.012))
        min_span = int(img_w * 0.70)
        wide_rows = 0
        for row in body_roi:
            xs = np.flatnonzero(row)
            if xs.size < min_row_ink:
                continue
            if int(xs[-1]) - int(xs[0]) >= min_span:
                wide_rows += 1
                if wide_rows >= 6:
                    return True
        return False

    for region in regions:
        semantic_flags = set(getattr(region, "semantic_flags", ()) or ())
        x0, y0, x1, y1 = region.bbox
        if y1 - y0 <= page_height * 0.08:
            continue
        if x1 - x0 >= page_width * 0.72:
            continue
        py0 = max(0, int(y0 * scale_y))
        py1 = min(img_h, int(y1 * scale_y))
        if py1 - py0 < 12:
            continue
        roi = ink[py0:py1, :]
        has_wide_content = _has_wide_non_header_ink(roi)
        if "visual_context" not in semantic_flags and not has_wide_content:
            continue
        col_counts = roi.sum(axis=0)
        min_pixels = max(3, int((py1 - py0) * 0.003))
        xs = np.flatnonzero(col_counts >= min_pixels)
        if xs.size == 0:
            continue

        runs: List[Tuple[int, int]] = []
        run_start = int(xs[0])
        prev = int(xs[0])
        max_inside_gap = max(2, int(img_w * 0.006))
        min_run_width = max(2, int(img_w * 0.002))
        for raw_x in xs[1:]:
            curr = int(raw_x)
            if curr - prev > max_inside_gap:
                if prev - run_start + 1 >= min_run_width:
                    runs.append((run_start, prev))
                run_start = curr
            prev = curr
        if prev - run_start + 1 >= min_run_width:
            runs.append((run_start, prev))
        if not runs:
            continue

        orig_px0 = max(0, int(x0 * scale_x))
        orig_px1 = min(img_w - 1, int(x1 * scale_x))
        selected = [
            run for run in runs
            if run[1] >= orig_px0 and run[0] <= orig_px1
        ]
        if not selected:
            continue

        max_neighbor_gap = max(8, int(img_w * 0.18))
        changed = True
        while changed:
            changed = False
            sel_x0 = min(run[0] for run in selected)
            sel_x1 = max(run[1] for run in selected)
            for run in runs:
                if run in selected:
                    continue
                gap = max(run[0] - sel_x1, sel_x0 - run[1], 0)
                if gap <= max_neighbor_gap:
                    selected.append(run)
                    changed = True

        vx0 = max(0.0, (float(min(run[0] for run in selected)) / scale_x) - pad)
        vx1 = min(page_width, (float(max(run[1] for run in selected)) / scale_x) + pad)
        if vx1 - vx0 <= (x1 - x0) * 1.08:
            continue
        new_bbox = (min(x0, vx0), y0, max(x1, vx1), y1)
        if has_wide_content:
            _add_region_semantic_flag(region, "wide_content")
        if hasattr(region, "set_display_bbox"):
            region.set_display_bbox(new_bbox)
        else:
            region.bbox = new_bbox


def _expand_commercial_written_response_answer_space(
    regions: List,
    *,
    page_width: float,
    page_height: float,
) -> None:
    if not regions or page_width <= 0 or page_height <= 0:
        return
    min_height = page_height * 0.165
    same_column_gap = page_width * 0.08
    for region in sorted(regions, key=lambda item: (item.bbox[1], item.bbox[0])):
        flags = set(getattr(region, "semantic_flags", ()) or ())
        should_include_answer_space = (
            "written_response" in flags
            or (
                "short_workbook_prompt" in flags
                and "visual_context" not in flags
            )
        )
        if not should_include_answer_space:
            continue
        source_bbox = getattr(region, "body_bbox", None) or region.bbox
        x0, y0, x1, y1 = source_bbox
        if y1 - y0 >= min_height:
            continue
        if y1 - y0 < page_height * 0.10 and "reasoning_response" not in flags:
            continue
        center = (x0 + x1) / 2
        next_tops = []
        for other in regions:
            if other is region:
                continue
            other_bbox = getattr(other, "body_bbox", None) or other.bbox
            ox0, oy0, ox1, _ = other_bbox
            if oy0 <= y0:
                continue
            other_center = (ox0 + ox1) / 2
            if abs(other_center - center) > same_column_gap and not (
                ox0 <= center <= ox1
            ):
                continue
            next_tops.append(oy0)
        next_top = min(next_tops) if next_tops else page_height
        target_y1 = min(page_height, next_top - page_height * 0.01, y0 + min_height)
        if target_y1 <= y1:
            continue
        new_bbox = (x0, y0, x1, target_y1)
        _add_region_semantic_flag(region, "answer_space")
        old_body = getattr(region, "body_bbox", None)
        old_audit = getattr(region, "audit_bbox", None)
        try:
            region.body_bbox = new_bbox
            if old_audit == old_body or old_audit == source_bbox:
                region.audit_bbox = new_bbox
        except Exception:
            pass
        if hasattr(region, "set_display_bbox"):
            display_bbox = getattr(region, "display_bbox", None) or region.bbox
            if display_bbox == source_bbox:
                region.set_display_bbox(new_bbox)
            else:
                dx0, dy0, dx1, _ = display_bbox
                region.set_display_bbox((dx0, dy0, dx1, max(display_bbox[3], target_y1)))
        else:
            region.bbox = new_bbox


def _prefer_commercial_later_shared_body_display(
    regions: List,
    *,
    page_height: float = 0.0,
) -> None:
    """Avoid rectangular crops that include previous subquestions in shared groups."""
    for region in regions:
        flags = set(getattr(region, "semantic_flags", ()) or ())
        if "shared_context_later" not in flags:
            continue
        body_bbox = getattr(region, "body_bbox", None)
        if body_bbox is None:
            continue
        body_starts_low = page_height > 0 and body_bbox[1] >= page_height * 0.60
        should_prefer_body = (
            "written_response" in flags
            or "references_prior_context" not in flags
            or body_starts_low
        )
        if not should_prefer_body:
            continue
        _add_region_semantic_flag(region, "shared_body_display")
        if hasattr(region, "set_display_bbox"):
            region.set_display_bbox(body_bbox)
        else:
            region.bbox = body_bbox


def _prefer_commercial_first_shared_context_display(
    regions: List,
    *,
    page_height: float = 0.0,
) -> None:
    """Use the prepared shared context crop for the first written subquestion."""
    for region in regions:
        flags = set(getattr(region, "semantic_flags", ()) or ())
        if "shared_context_first" not in flags:
            continue
        if "written_response" not in flags and "short_workbook_prompt" not in flags:
            continue
        context_bbox = getattr(region, "context_bbox", None)
        if context_bbox is None:
            continue
        display_bbox = getattr(region, "display_bbox", None) or region.bbox
        context_height = context_bbox[3] - context_bbox[1]
        display_height = display_bbox[3] - display_bbox[1]
        if context_height <= display_height * 1.10:
            continue
        target_height = context_height
        if page_height > 0:
            target_height = min(context_height, max(display_height, page_height * 0.35))
        if target_height <= display_height * 1.10:
            continue
        target_bbox = (
            context_bbox[0],
            context_bbox[1],
            context_bbox[2],
            min(context_bbox[3], context_bbox[1] + target_height),
        )
        _add_region_semantic_flag(region, "shared_context_answer_space")
        if hasattr(region, "set_display_bbox"):
            region.set_display_bbox(target_bbox)
        else:
            region.bbox = target_bbox


def _trim_other_source_text_regions_to_ink(
    image_path: str,
    regions: List,
    *,
    page_width: float,
    page_height: float,
    source_type: Optional[str],
) -> None:
    """Trim overextended school/exam text-PDF crops to the visible content island.

    ``other`` tenant-2 PDFs include school exams whose last written-response
    question can be classified as single-column when the opposite column is
    blank. The text splitter then stretches the crop to the footer/copyright
    note. This keeps product display boxes rectangular, but drops isolated
    footer bands after a large blank gap and shrinks x to the selected content.
    """
    if source_type != "other" or not regions or page_width <= 0 or page_height <= 0:
        return
    gray = cv2.imread(image_path, cv2.IMREAD_GRAYSCALE)
    if gray is None or gray.size == 0:
        return

    img_h, img_w = gray.shape[:2]
    scale_x = img_w / page_width
    scale_y = img_h / page_height
    ink = gray < 245
    frame_band_x = max(2, int(img_w * 0.025))
    frame_band_y = max(2, int(img_h * 0.018))
    ink[:, :frame_band_x] = False
    ink[:, img_w - frame_band_x:] = False
    ink[:frame_band_y, :] = False
    ink[img_h - frame_band_y:, :] = False
    column_refs: list[tuple[float, float, float, float]] = []
    for other in regions:
        obox = getattr(other, "display_bbox", None) or other.bbox
        ox0, oy0, ox1, oy1 = obox
        ow = ox1 - ox0
        if page_width * 0.25 <= ow <= page_width * 0.65:
            column_refs.append((ox0, oy0, ox1, oy1))

    def _active_runs(row_active: np.ndarray, *, max_gap: int) -> list[tuple[int, int]]:
        ys = np.flatnonzero(row_active)
        if ys.size == 0:
            return []
        runs: list[tuple[int, int]] = []
        start = int(ys[0])
        prev = int(ys[0])
        for raw_y in ys[1:]:
            y = int(raw_y)
            if y - prev > max_gap:
                runs.append((start, prev))
                start = y
            prev = y
        runs.append((start, prev))
        return runs

    for region in regions:
        flags = set(getattr(region, "semantic_flags", ()) or ())
        x0, y0, x1, y1 = getattr(region, "display_bbox", None) or region.bbox
        region_w = x1 - x0
        region_h = y1 - y0
        if region_h < page_height * 0.30 and region_w < page_width * 0.78:
            continue
        if "shared_group" in flags:
            continue

        px0 = max(0, int(x0 * scale_x))
        px1 = min(img_w, int(x1 * scale_x))
        py0 = max(0, int(y0 * scale_y))
        py1 = min(img_h, int(y1 * scale_y))
        if px1 - px0 < 20 or py1 - py0 < 30:
            continue

        roi = ink[py0:py1, px0:px1]
        min_row_ink = max(8, int((px1 - px0) * 0.010))
        row_active = roi.sum(axis=1) >= min_row_ink
        runs = _active_runs(
            row_active,
            max_gap=max(8, int(img_h * 0.018)),
        )
        if not runs:
            continue

        kept = [runs[0]]
        for run in runs[1:]:
            prev = kept[-1]
            gap = run[0] - prev[1]
            kept_height = kept[-1][1] - kept[0][0]
            absolute_run_y = py0 + run[0]
            footer_like_late_band = (
                absolute_run_y >= img_h * 0.78
                and gap > img_h * 0.06
            )
            detached_far_band = gap > img_h * 0.20
            if footer_like_late_band or detached_far_band:
                break
            kept.append(run)

        keep_y0 = min(run[0] for run in kept)
        keep_y1 = max(run[1] for run in kept)
        selected = roi[keep_y0:keep_y1 + 1, :]
        min_col_ink = max(4, int(selected.shape[0] * 0.006))
        xs = np.flatnonzero(selected.sum(axis=0) >= min_col_ink)
        if xs.size == 0:
            continue

        pad_x = page_width * 0.012
        pad_bottom = page_height * 0.025
        new_x0 = max(0.0, (px0 + int(xs[0])) / scale_x - pad_x)
        new_x1 = min(page_width, (px0 + int(xs[-1])) / scale_x + pad_x)
        new_y0 = y0
        new_y1 = min(y1, (py0 + keep_y1) / scale_y + pad_bottom)

        if new_y1 - new_y0 < page_height * 0.08:
            continue
        if (y1 - new_y1) < page_height * 0.08 and (x1 - x0) - (new_x1 - new_x0) < page_width * 0.12:
            continue
        if region_w > page_width * 0.78 and column_refs:
            for rx0, _ry0, rx1, _ry1 in column_refs:
                if abs(x0 - rx0) <= page_width * 0.08:
                    new_x1 = min(new_x1, rx1 + page_width * 0.025)
                    break
                if abs(x1 - rx1) <= page_width * 0.08:
                    new_x0 = max(new_x0, rx0 - page_width * 0.025)
                    break

        new_bbox = (
            max(0.0, new_x0 if new_x0 < x0 else x0),
            new_y0,
            max(new_x1, new_x0 + 1.0),
            new_y1,
        )
        _add_region_semantic_flag(region, "ink_trimmed")
        if hasattr(region, "set_display_bbox"):
            region.set_display_bbox(new_bbox)
        else:
            region.bbox = new_bbox


def _regions_already_span_columns(regions: List, *, page_width: float) -> bool:
    """Skip single-page visual x expansion when splitter already found columns."""
    if page_width <= 0 or len(regions) < 2:
        return False
    mid_x = page_width * 0.5
    has_left = False
    has_right = False
    for region in regions:
        x0, _, x1, _ = region.bbox
        width = x1 - x0
        if width > page_width * 0.68:
            return False
        center_x = (x0 + x1) / 2
        if center_x < mid_x:
            has_left = True
        else:
            has_right = True
    return has_left and has_right


def _should_expand_text_regions_by_visual_x(
    paper_type_result: object | None,
    paper_type_debug: Dict | None,
) -> bool:
    """시각 x축 보강 대상 여부.

    text 기준 dual/quad 신호가 없으면 pixel-dual은 전폭 그림/표 때문에 생긴
    오분류일 수 있다. 이런 페이지는 region 개수는 유지하되 x축만 렌더 이미지로
    보강한다.
    """
    if bool(getattr(paper_type_result, "is_quadrant", False)):
        return False
    debug = paper_type_debug or {}
    if bool(debug.get("is_dual_text")):
        return False
    return True


def _is_pdf(file_path: str) -> bool:
    """파일 확장자 또는 매직 바이트로 PDF 여부 판단."""
    if file_path.lower().endswith(".pdf"):
        return True
    try:
        with open(file_path, "rb") as f:
            header = f.read(5)
            return header == b"%PDF-"
    except Exception:
        return False


def _bias_handwriting_score(source_type: Optional[str]) -> Optional[float]:
    """source_type 신호 → handwriting_score bias 변환.

    학생답안지 폰사진은 손글씨 + perspective + 회전이 본질이므로 픽셀 휴리스틱
    실패해도 STUDENT_ANSWER_PHOTO 분류를 강제. 0.85는 classify_paper_type의
    0.78 임계값을 안정 통과시키는 값. 다른 source_type은 픽셀/텍스트 휴리스틱
    그대로 사용 (None).
    """
    if source_type == "student_exam_photo":
        return 0.85
    return None


# Stage 6.3Q (2026-05-07) — segment_opencv mild LAB-CLAHE wiring
#
# manual/auto 전처리 비대칭 fix. manual cut 경로 (matchup_manual_index._preprocess_camera_image)
# 만 CLAHE+deskew+Unsharp 적용되고 auto 경로는 raw 였음 → 학원장 노가다 의존 비대칭.
# Stage 6.3P dry-run 측정 (3 sample): mild LAB-CLAHE 가 scan PDF 에서 contour +67%, IoU 0.988.
# 카메라 단독 적용은 IoU 0.18 (deskew 결합 필요 — Stage 6.3R 영역).
#
# ENV gate `MATCHUP_SEGMENT_OPENCV_CLAHE`:
#   "disabled" (default) — 회귀 0, manual 경로만 보존
#   "scan_only"          — has_embedded_text=False (스캔 PDF) 또는
#                          source_type=student_exam_photo 일 때만 적용
#   "all"                — 모든 segment_opencv 호출에 적용 (실험용)
#
# 회귀 임계 (dry-run 측정): 카메라 IoU>=0.5 / 스캔본 IoU>=0.95 / 깨끗 PDF IoU>=0.85.
# clean text PDF 페이지는 OCR 경로 안 타고 text_boxes 가 우선 — opencv 폴백 도달 자체가 드물지만
# scan_only 모드는 has_embedded_text=True 인 페이지를 자동 제외.
def _segment_opencv_clahe_mode() -> str:
    return (os.environ.get("MATCHUP_SEGMENT_OPENCV_CLAHE", "disabled") or "disabled").lower()


def _should_apply_opencv_clahe(
    *,
    has_embedded_text: bool,
    source_type: Optional[str],
) -> bool:
    """ENV gate + has_embedded_text + source_type 으로 CLAHE 적용 여부 결정.

    호출자: _segment_single_image / _boxes_and_regions_for_pdf_page (opencv 폴백 진입 시).
    detect_dual_column_pixel (paper_type 백업 분류기) 도 같은 게이트.
    """
    mode = _segment_opencv_clahe_mode()
    if mode == "disabled":
        return False
    if mode == "all":
        return True
    if mode == "scan_only":
        # 스캔 PDF (text 없음) 또는 학생 시험지 사진. clean text PDF 는 미적용.
        return (not has_embedded_text) or (source_type == "student_exam_photo")
    return False


# 워커 작업당 생성된 pdf-seg-* tmp 디렉터리들을 추적 → dispatcher의 finally가 일괄 정리.
# 작업이 동시 실행되더라도 각 작업이 독립 contextvar token을 보유하므로 안전.
_PDF_SEG_TMP_DIRS: "contextvars.ContextVar[List[str] | None]" = contextvars.ContextVar(
    "pdf_seg_tmp_dirs", default=None,
)


def begin_pdf_seg_scope() -> None:
    """dispatcher가 작업 시작 시 호출 — 빈 리스트로 스코프 시작."""
    _PDF_SEG_TMP_DIRS.set([])


def register_pdf_seg_tmp_dirs(dirs: List[str]) -> None:
    """tmp_dirs를 현재 스코프에 누적 등록. dispatcher의 finally가 일괄 cleanup.

    호출 시점: _pdf_to_images의 mkdtemp 직후(예외 안전망) + 파이프라인의 multipage 결과 수신 후.
    동일 dir 중복 등록은 무해 (cleanup_pdf_seg_tmp_dirs는 prefix 검증 + ignore_errors).

    no-scope: 워커 entrypoint를 거치지 않은 호출(테스트/스크립트). 여기서는 leak warn만
    남기고 정리는 호출자 책임 — 즉시 cleanup하면 호출자가 아직 dir을 사용 중일 때 파일이 사라짐.
    """
    if not dirs:
        return
    bucket = _PDF_SEG_TMP_DIRS.get()
    if bucket is None:
        logger.warning(
            "register_pdf_seg_tmp_dirs called outside scope — caller must cleanup: %s",
            dirs,
        )
        return
    bucket.extend(dirs)


def cleanup_registered_pdf_seg_tmp_dirs() -> None:
    """dispatcher의 finally가 호출 — 누적된 tmp_dirs를 일괄 정리."""
    bucket = _PDF_SEG_TMP_DIRS.get()
    if bucket:
        cleanup_pdf_seg_tmp_dirs(bucket)
    _PDF_SEG_TMP_DIRS.set(None)


def cleanup_pdf_seg_tmp_dirs(tmp_dirs: List[str]) -> None:
    """_pdf_to_images가 만든 mkdtemp 디렉터리들을 통째 제거.

    안전 가드: prefix가 "pdf-seg-"이고 tmp 루트 하위인 경로만 삭제.
    """
    if not tmp_dirs:
        return
    try:
        tmp_root = Path(tempfile.gettempdir()).resolve()
    except Exception:
        return
    for d in tmp_dirs:
        if not d:
            continue
        try:
            p = Path(d).resolve()
            if not p.name.startswith("pdf-seg-"):
                continue
            try:
                p.relative_to(tmp_root)
            except (ValueError, OSError):
                logger.warning("cleanup_pdf_seg skip — outside tmp root: %s", p)
                continue
            shutil.rmtree(p, ignore_errors=True)
        except Exception as e:
            logger.warning("cleanup_pdf_seg failed: dir=%s err=%s", d, e)


def _pdf_to_images(
    pdf_path: str,
    *,
    handwriting_bias: Optional[float] = None,
    source_type: Optional[str] = None,
) -> Tuple[List[Dict], str]:
    """
    PDF 파일의 각 페이지를 이미지로 변환 + 텍스트 기반 문항 박스 사전 계산.

    Returns:
        (
          [
            {
              "image_path": str,
              "has_embedded_text": bool,
              "text_boxes": List[BBox]  # 텍스트 기반 분할 박스 (픽셀 좌표계). 비었으면 실패.
            },
            ...
          ],
          tmp_dir: str  # 호출자가 cleanup_pdf_seg_tmp_dirs로 정리해야 함
        )
    """
    from academy.adapters.tools.pymupdf_renderer import PdfDocument
    from academy.domain.tools.paper_type import (
        PaperType,
        classify_paper_type,
    )
    from academy.domain.tools.question_splitter import (
        count_marginal_anchor_candidates,
        split_questions,
        TextBlock as SplitterTextBlock,
    )

    results: List[Dict] = []
    tmp_dir = tempfile.mkdtemp(prefix="pdf-seg-")
    # 즉시 추적 등록 — 이후 PDF 렌더 중 예외가 나도 dispatcher finally가 정리.
    # 호출자가 register_pdf_seg_tmp_dirs를 호출해도 동일 dir 중복 등록은 무해
    # (cleanup은 prefix + 존재 여부 검증 후 rmtree, 동일 dir 두 번 처리해도 ignore_errors).
    register_pdf_seg_tmp_dirs([tmp_dir])

    # ── Phase 1: 페이지별 텍스트 + paper_type 수집 (split_questions 호출 X) ──
    # Phase 2 doc-level workbook 감지 후 Phase 3 에서 일괄 split.
    phase1: List[Dict] = []
    with PdfDocument(pdf_path) as doc:
        page_count = doc.page_count()
        logger.info("PDF_TO_IMAGES | pages=%d | path=%s", page_count, pdf_path)

        for i in range(page_count):
            pil_img = doc.render_page(i, dpi=200)
            out_path = os.path.join(tmp_dir, f"page_{i:03d}.png")
            pil_img.save(out_path, "PNG")

            has_text = False
            try:
                raw_blocks = doc.extract_text_blocks(i)
                has_text = len(raw_blocks) > 0
            except Exception:
                raw_blocks = []

            tbs: List[SplitterTextBlock] = []
            pw, ph = 0.0, 0.0
            page_paper_type = PaperType.UNKNOWN.value
            paper_type_debug: Dict = {}
            pt = None
            is_skip_page = False
            if has_text:
                try:
                    tbs = [
                        SplitterTextBlock(text=b.text, x0=b.x0, y0=b.y0, x1=b.x1, y1=b.y1)
                        for b in raw_blocks
                    ]
                    pw, ph = doc.page_dimensions(i)
                    pt = classify_paper_type(
                        text_blocks=tbs,
                        image_path=out_path,
                        page_width=pw,
                        page_height=ph,
                        has_embedded_text=True,
                        handwriting_score=handwriting_bias,
                    )
                    page_paper_type = pt.paper_type.value
                    paper_type_debug = pt.debug
                    if pt.is_non_question:
                        is_skip_page = True
                        logger.info(
                            "PDF_TEXT_NON_QUESTION_PAGE | page=%d | skip=True | "
                            "paper_type=%s",
                            i, page_paper_type,
                        )
                except Exception as e:
                    logger.warning(
                        "PDF_TEXT_CLASSIFY_ERROR | page=%d | error=%s", i, e,
                    )

            phase1.append({
                "page_index": i,
                "image_path": out_path,
                "has_text": has_text,
                "text_blocks": tbs,
                "page_text": "\n".join(b.text for b in tbs)[:8000] if tbs else "",
                "page_width": pw,
                "page_height": ph,
                "paper_type": page_paper_type,
                "paper_type_debug": paper_type_debug,
                "paper_type_result": pt,
                "is_skip_page": is_skip_page,
            })

    # ── Phase 2: doc-level workbook(per-page-restart) 감지 ──
    # 신호 A: 페이지마다 marginal column "N." block 분포 (PyMuPDF block 추출 결과
    #         의존 — 페이지별 일관성 변동 큼).
    # 신호 B: 1차 split 결과 anchor number 분포 (per-page-restart 패턴 — 페이지마다
    #         anchor 1, 2, 3... 리셋). marginal block 검출 불안정 보완.
    # 둘 중 하나라도 True → workbook 강제 → 2차 split(prefer_marginal=True).
    pages_with_marginal = 0
    eligible_pages = 0  # text 있고 non-question 아닌 페이지
    for p in phase1:
        if not p["has_text"] or p["is_skip_page"] or not p["text_blocks"]:
            continue
        eligible_pages += 1
        m_count = count_marginal_anchor_candidates(
            p["text_blocks"], p["page_width"], p["page_height"],
        )
        if m_count >= 1:
            pages_with_marginal += 1
    signal_a = False
    if eligible_pages >= 5:
        ratio = pages_with_marginal / eligible_pages
        signal_a = ratio >= 0.3 and pages_with_marginal >= 3

    # 1차 split (prefer_marginal=False) — anchor 분포 수집.
    first_pass_regions: List[List] = []
    for p in phase1:
        if not p["has_text"] or p["is_skip_page"] or not p["text_blocks"]:
            first_pass_regions.append([])
            continue
        try:
            regions = split_questions(
                p["text_blocks"], p["page_width"], p["page_height"],
                page_index=p["page_index"],
                paper_type=p["paper_type_result"],
                prefer_marginal=False,
            )
            first_pass_regions.append(list(regions))
        except Exception:
            first_pass_regions.append([])

    # 신호 B — per-page-restart 패턴 (page-level dedup 안 한 anchor list 기반).
    pages_per_number_b: dict = {}
    for page_regions in first_pass_regions:
        for n in {r.number for r in page_regions}:
            pages_per_number_b[n] = pages_per_number_b.get(n, 0) + 1
    pages_with_low_b = sum(
        1 for page_regions in first_pass_regions
        if {r.number for r in page_regions} & {1, 2, 3}
    )
    eligible_with_anchors = sum(1 for r in first_pass_regions if r)
    signal_b = False
    if eligible_with_anchors >= 5:
        # anchor 1/2/3 이 30%+ 페이지에 등장 AND 절대 3+ 페이지 = workbook 패턴
        ratio_low = pages_with_low_b / eligible_with_anchors
        signal_b = ratio_low >= 0.3 and pages_with_low_b >= 3

    workbook_doc = signal_a or signal_b
    logger.info(
        "PDF_WORKBOOK_DETECT | eligible=%d/%d | marginal_pages=%d | "
        "low_anchor_pages=%d | signal_a=%s | signal_b=%s | workbook=%s",
        eligible_with_anchors, eligible_pages, pages_with_marginal,
        pages_with_low_b, signal_a, signal_b, workbook_doc,
    )

    # ── Phase 3: 2차 split (workbook 일 때 prefer_marginal=True) ──
    for idx, p in enumerate(phase1):
        text_boxes: List[BBox] = []
        text_box_meta: List[Dict[str, object]] = []
        text_regions: List = []
        if p["has_text"] and not p["is_skip_page"] and p["text_blocks"]:
            try:
                if workbook_doc:
                    # 워크북: marginal anchor 우선 — Q 단위 cut.
                    regions = split_questions(
                        p["text_blocks"], p["page_width"], p["page_height"],
                        page_index=p["page_index"],
                        paper_type=p["paper_type_result"],
                        prefer_marginal=True,
                    )
                else:
                    # 시험지: 1차 결과 그대로.
                    regions = first_pass_regions[idx]
                if source_type == "commercial_workbook":
                    _expand_commercial_written_response_answer_space(
                        regions,
                        page_width=p["page_width"],
                        page_height=p["page_height"],
                    )
                    _prefer_commercial_first_shared_context_display(
                        regions,
                        page_height=p["page_height"],
                    )
                    _prefer_commercial_later_shared_body_display(
                        regions,
                        page_height=p["page_height"],
                    )
                if _should_expand_text_regions_by_visual_x(
                    p.get("paper_type_result"),
                    p.get("paper_type_debug"),
                ):
                    _expand_single_text_regions_to_visual_content(
                        p["image_path"],
                        regions,
                        page_width=p["page_width"],
                        page_height=p["page_height"],
                    )
                _trim_other_source_text_regions_to_ink(
                    p["image_path"],
                    regions,
                    page_width=p["page_width"],
                    page_height=p["page_height"],
                    source_type=source_type,
                )
                text_regions = list(regions)
                for r in regions:
                    display_bbox = getattr(r, "display_bbox", None) or r.bbox
                    text_boxes.append(_bbox_points_to_pixels(display_bbox))
                    text_box_meta.append(_region_bbox_meta(r))
                logger.info(
                    "PDF_TEXT_LAYOUT | page=%d | paper_type=%s | regions=%d | "
                    "workbook=%s",
                    p["page_index"], p["paper_type"], len(regions), workbook_doc,
                )
            except Exception as e:
                logger.warning(
                    "PDF_TEXT_BOXES_ERROR | page=%d | error=%s",
                    p["page_index"], e,
                )

        results.append({
            "image_path": p["image_path"],
            "has_embedded_text": p["has_text"],
            "text_boxes": text_boxes,
            "text_box_meta": text_box_meta,
            "text_regions": text_regions,
            "page_text": p.get("page_text") or "",
            "is_skip_page": p["is_skip_page"],
            "paper_type": p["paper_type"],
            "paper_type_debug": p["paper_type_debug"],
        })

    return results, tmp_dir


def _segment_single_image(
    image_path: str,
    *,
    skip_ocr: bool = False,
    is_pdf_page: bool = False,
    source_type: str | None = None,
    has_embedded_text: bool = False,
) -> List[BBox]:
    """
    단일 이미지에 대한 세그멘테이션 (엔진 자동 선택).

    auto 모드 우선순위: YOLO(모델+PDF페이지) → OCR(크레덴셜 있을 때, skip_ocr=False) → OpenCV.
    OCR 경로는 스캔본 시험지에서 문항 번호 감지를 통해 페이지당 여러 문항을 분할.

    skip_ocr: PDF 페이지에 embedded text가 존재할 때 True. OCR 비용을 아낀다
              (pdf_question_pipeline이 PDF 텍스트로 별도 분할을 수행하기 때문).
    is_pdf_page: True면 PDF에서 렌더링된 페이지. False면 사용자가 직접 업로드한
                 단일 이미지(카메라 촬영일 가능성). 카메라 사진은 YOLO 학습 분포를
                 벗어나므로 YOLO를 건너뛰고 OCR/OpenCV 경로 사용.
    source_type: 양식 신호 (P1.5, 2026-05-06) — segment_questions_yolo 양식별 conf 분기.
                 commercial_workbook / academy_workbook / student_exam_photo / school_exam_pdf.
                 호출 chain 점진 적용 — None 시 default conf (회귀 0).
    has_embedded_text: PDF 페이지에 embedded text가 있는지 (Stage 6.3Q). opencv 폴백 진입 시
                       _should_apply_opencv_clahe 게이트 입력. 단일 이미지는 항상 False.
    """
    cfg = AIConfig.load()
    engine = (cfg.QUESTION_SEGMENTATION_ENGINE or "auto").lower()

    apply_clahe = _should_apply_opencv_clahe(
        has_embedded_text=has_embedded_text, source_type=source_type,
    )

    if engine == "opencv":
        return segment_questions_opencv(image_path, apply_clahe=apply_clahe)
    if engine == "yolo":
        return segment_questions_yolo(image_path, source_type=source_type)
    if engine == "ocr":
        return segment_questions_ocr(image_path)

    # auto 모드: YOLO는 PDF 페이지에만 사용 (카메라 사진 오탐 방지)
    if is_pdf_page:
        try:
            boxes = segment_questions_yolo(image_path, source_type=source_type)
            if boxes:
                return boxes
        except Exception:
            pass

    if not skip_ocr and is_ocr_available():
        try:
            boxes = segment_questions_ocr(image_path)
            if boxes:
                return boxes
        except Exception as e:
            logger.warning("OCR_SEGMENT_AUTO_FAIL | path=%s | error=%s", image_path, e)

    return segment_questions_opencv(image_path, apply_clahe=apply_clahe)


def _boxes_and_regions_for_pdf_page(
    page_info: Dict, page_index: int,
    *,
    handwriting_bias: Optional[float] = None,
    source_type: Optional[str] = None,
) -> Tuple[List[BBox], List]:
    """
    PDF 페이지 1개에 대한 최종 박스 + QuestionRegion (번호 포함) 반환.

    regions는 크로스-페이지 anchor 검증에 쓰이며, 번호가 없는
    (OpenCV fallback) 경우 빈 리스트로 반환.

    우선순위:
      1. 텍스트 기반 분할 성공 → text_boxes + text_regions 사용
      2. 스캔본 + OCR 가용 → OCR 결과 (boxes + numbered regions)
      3. OCR 불가 / 예외 → OpenCV 안전망 (번호 없음)

    OCR 경로에서 paper_type을 별도 분류하여 page_info["paper_type"]에 보존
    (segment_questions_ocr_regions은 boxes만 반환하므로 paper_type 정보가
    유실되어 _aggregate_paper_types가 unknown으로 떨어뜨리는 결함 차단).
    """
    from academy.domain.tools.question_splitter import QuestionRegion

    if page_info["text_boxes"]:
        return list(page_info["text_boxes"]), list(page_info.get("text_regions") or [])

    # text-PDF에서 is_non_question_page=True로 판정된 페이지는 OCR/OpenCV 안전망에서도
    # problem 박스를 만들지 않음. 표지/목차/안내가 sequential global_number로 잘리는 leak 차단.
    if page_info.get("is_skip_page"):
        return [], []

    image_path = page_info["image_path"]

    # Born-digital PDF pages with embedded text should not be resurrected by
    # OpenCV when the text splitter found no anchors. In tenant-2 workbooks
    # this path turned concept/explanation pages into large false problems.
    if page_info.get("has_embedded_text") and (page_info.get("page_text") or "").strip():
        logger.info(
            "PDF_TEXT_SPLIT_EMPTY_DROP | page=%d | paper_type=%s | chars=%d",
            page_index,
            page_info.get("paper_type") or "unknown",
            len(page_info.get("page_text") or ""),
        )
        return [], []

    # 스캔본에서 OCR 가용 시 — OCR 결과 신뢰
    if not page_info["has_embedded_text"] and is_ocr_available():
        try:
            from academy.adapters.ai.detection.segment_ocr import (
                segment_questions_ocr_regions,
            )
            raw = segment_questions_ocr_regions(image_path)
            boxes: List[BBox] = []
            regions: List = []
            for x0, y0, x1, y1, qnum in raw:
                boxes.append((int(x0), int(y0), int(x1 - x0), int(y1 - y0)))
                regions.append(QuestionRegion(
                    number=int(qnum),
                    bbox=(float(x0), float(y0), float(x1), float(y1)),
                    page_index=page_index,
                ))
            # OCR 경로 paper_type 보존 — segment_questions_ocr_regions이 메타를
            # 돌려주지 않으므로 dispatcher가 별도 classify_paper_type 호출.
            _classify_and_record_paper_type(
                page_info, image_path,
                has_embedded_text=False,
                handwriting_bias=handwriting_bias,
            )
            return boxes, regions  # 빈 결과도 trust (non-question page)
        except Exception as e:
            logger.warning(
                "PDF_PAGE_OCR_FAIL | path=%s | error=%s",
                image_path, e,
            )
            # fallthrough → OpenCV 안전망

    if not page_info["has_embedded_text"]:
        _classify_and_record_paper_type(
            page_info, image_path,
            has_embedded_text=False,
            handwriting_bias=handwriting_bias,
        )

    if (
        not page_info["has_embedded_text"]
        and (
            source_type == "school_exam_pdf"
            or page_info.get("paper_type") in {"scan_single", "scan_dual"}
        )
    ):
        apply_clahe = _should_apply_opencv_clahe(
            has_embedded_text=False,
            source_type=source_type,
        )
        if page_info.get(_SCAN_LAYOUT_USE_FRAGMENT_MERGE):
            cached_boxes = page_info.get(_SCAN_LAYOUT_BOXES_FRAGMENT_MERGED)
        else:
            cached_boxes = page_info.get(_SCAN_LAYOUT_BOXES_DEFAULT)
        boxes = (
            list(cached_boxes)
            if cached_boxes is not None
            else segment_questions_scan_layout(
                image_path,
                apply_clahe=apply_clahe,
                merge_fragmented_columns=bool(page_info.get(_SCAN_LAYOUT_USE_FRAGMENT_MERGE)),
            )
        )
        if boxes:
            return boxes, []

    # 텍스트 있지만 분할 실패 OR OCR 크레덴셜 없음 OR OCR 예외
    skip_ocr = page_info["has_embedded_text"]
    boxes = _segment_single_image(
        image_path,
        skip_ocr=skip_ocr,
        is_pdf_page=True,
        source_type=source_type,
        has_embedded_text=bool(page_info.get("has_embedded_text")),
    )
    boxes = _filter_cover_like_boxes(boxes, image_path, page_index)
    # OpenCV fallback / OCR 예외 경로도 paper_type 분류 시도 (image_path 기반).
    _classify_and_record_paper_type(
        page_info, image_path,
        has_embedded_text=bool(page_info.get("has_embedded_text")),
        handwriting_bias=handwriting_bias,
    )
    return boxes, []  # OpenCV fallback — 번호 없음


def _classify_and_record_paper_type(
    page_info: Dict, image_path: str,
    *,
    has_embedded_text: bool,
    handwriting_bias: Optional[float],
) -> None:
    """page_info에 paper_type 정보가 비어 있으면 image_path 기반으로 분류 후 저장.

    classify_paper_type을 텍스트 블록 없이 호출하면 이미지 픽셀 휴리스틱과
    handwriting bias만 사용. STUDENT_ANSWER_PHOTO 분기는 bias 0.78+로 진입.
    """
    if page_info.get("paper_type") and page_info.get("paper_type") != "unknown":
        return  # 이미 PDF 텍스트 경로에서 분류됨
    try:
        from PIL import Image as _PILImage  # 지연 import — opencv와 충돌 방지
        from academy.domain.tools.paper_type import classify_paper_type
        with _PILImage.open(image_path) as img:
            w, h = img.size
        pt = classify_paper_type(
            text_blocks=None,
            image_path=image_path,
            page_width=float(w),
            page_height=float(h),
            has_embedded_text=has_embedded_text,
            handwriting_score=handwriting_bias,
        )
        page_info["paper_type"] = pt.paper_type.value
        page_info["paper_type_debug"] = pt.debug
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "PAPER_TYPE_CLASSIFY_FAIL | path=%s | err=%s",
            image_path, e,
        )


def _prime_scan_layout_boxes_for_pdf(
    page_infos: List[Dict],
    *,
    source_type: Optional[str],
    handwriting_bias: Optional[float],
) -> None:
    """Precompute scan-layout boxes and choose document-level fragment merging.

    Tenant-2 scan PDFs split into two buckets that look similar per page:
    dense school-exam scans, and textless workbook scans where one question is
    fragmented into stem/table/choices. Aggressive merging is enabled only when
    the whole document shows the latter pattern.
    """
    if not page_infos or is_ocr_available():
        return

    apply_clahe = _should_apply_opencv_clahe(
        has_embedded_text=False,
        source_type=source_type,
    )
    candidates: List[Dict] = []
    for info in page_infos:
        if info.get("has_embedded_text") or info.get("is_skip_page"):
            continue
        image_path = info.get("image_path")
        if not image_path:
            continue
        _classify_and_record_paper_type(
            info,
            image_path,
            has_embedded_text=False,
            handwriting_bias=handwriting_bias,
        )
        paper_type = info.get("paper_type")
        should_use_scan_layout = (
            source_type == "school_exam_pdf"
            or paper_type in {"scan_single", "scan_dual"}
        )
        if not should_use_scan_layout:
            continue
        info[_SCAN_LAYOUT_BOXES_DEFAULT] = segment_questions_scan_layout(
            image_path,
            apply_clahe=apply_clahe,
            merge_fragmented_columns=False,
        )
        if source_type != "school_exam_pdf" and paper_type == "scan_dual":
            info[_SCAN_LAYOUT_BOXES_FRAGMENT_MERGED] = segment_questions_scan_layout(
                image_path,
                apply_clahe=apply_clahe,
                merge_fragmented_columns=True,
            )
            candidates.append(info)

    if _should_use_fragmented_scan_workbook_merge(
        page_infos,
        source_type=source_type,
        candidates=candidates,
    ):
        for info in candidates:
            info[_SCAN_LAYOUT_USE_FRAGMENT_MERGE] = True


def _should_use_fragmented_scan_workbook_merge(
    page_infos: List[Dict],
    *,
    source_type: Optional[str],
    candidates: Optional[List[Dict]] = None,
) -> bool:
    if source_type == "school_exam_pdf":
        return False
    scan_pages = candidates if candidates is not None else [
        info for info in page_infos
        if not info.get("has_embedded_text")
        and info.get("paper_type") == "scan_dual"
        and _SCAN_LAYOUT_BOXES_DEFAULT in info
        and _SCAN_LAYOUT_BOXES_FRAGMENT_MERGED in info
    ]
    if len(scan_pages) < 8:
        return False

    active_pages = [
        info for info in page_infos
        if not info.get("is_skip_page")
    ]
    if len(scan_pages) / max(1, len(active_pages)) < 0.80:
        return False

    default_counts = [
        len(info.get(_SCAN_LAYOUT_BOXES_DEFAULT) or [])
        for info in scan_pages
    ]
    merged_counts = [
        len(info.get(_SCAN_LAYOUT_BOXES_FRAGMENT_MERGED) or [])
        for info in scan_pages
    ]
    if not default_counts or not merged_counts:
        return False

    default_avg = sum(default_counts) / len(default_counts)
    merged_avg = sum(merged_counts) / len(merged_counts)
    return (
        default_avg >= 4.0
        and merged_avg <= 3.2
        and (default_avg - merged_avg) >= 1.8
    )


def _filter_cover_like_boxes(
    boxes: List[BBox], image_path: str, page_index: int,
) -> List[BBox]:
    """OpenCV fallback이 단일 박스로 페이지 대부분을 묶어내는 케이스 필터.

    문항 anchor를 못 잡은 페이지(표지/목차/안내문 등)에서 OpenCV가 페이지
    전체를 1개 큰 박스로 반환하면 사용자에게는 "이상하게 잘린 표지"로 보인다.
    번호 정보가 없으니 실제 문항이 아님이 거의 확실 → 드롭.

    조건: 박스 1개 + 박스 면적이 페이지의 70% 이상.
    """
    if len(boxes) != 1:
        return boxes
    try:
        img = cv2.imread(image_path)
        if img is None:
            return boxes
        h_img, w_img = img.shape[:2]
        page_area = float(w_img * h_img)
        if page_area <= 0:
            return boxes
        x, y, w, h = boxes[0]
        ratio = (w * h) / page_area
        if ratio >= 0.70:
            logger.info(
                "PDF_COVER_LIKE_DROP | page=%d | ratio=%.2f | box=(%d,%d,%d,%d)",
                page_index, ratio, x, y, w, h,
            )
            return []
    except Exception as e:
        logger.warning("COVER_FILTER_ERROR | page=%d | error=%s", page_index, e)
    return boxes


def _collect_pdf_pages(
    image_path: str,
    *,
    source_type: Optional[str] = None,
) -> Tuple[List[Dict], List[List[BBox]], List[List], str]:
    """
    PDF의 모든 페이지를 처리해서 (page_infos, boxes_per_page, regions_per_page, tmp_dir)를 반환.
    크로스-페이지 anchor 검증을 적용해 spurious/outlier 박스를 제거.

    tmp_dir은 호출자가 cleanup_pdf_seg_tmp_dirs([tmp_dir])로 정리해야 함.

    source_type — 학원장 입력 신호. student_exam_photo면 handwriting_bias 0.85로
    classify_paper_type의 STUDENT_ANSWER_PHOTO 분기를 강제. 다른 source는 휴리스틱.
    """
    from academy.domain.tools.question_splitter import validate_anchors_across_pages

    handwriting_bias = _bias_handwriting_score(source_type)

    page_infos, tmp_dir = _pdf_to_images(
        image_path,
        handwriting_bias=handwriting_bias,
        source_type=source_type,
    )
    if not page_infos:
        return [], [], [], tmp_dir
    _prime_scan_layout_boxes_for_pdf(
        page_infos,
        source_type=source_type,
        handwriting_bias=handwriting_bias,
    )

    boxes_per_page: List[List[BBox]] = []
    regions_per_page: List[List] = []
    for page_idx, info in enumerate(page_infos):
        boxes, regions = _boxes_and_regions_for_pdf_page(
            info, page_idx,
            handwriting_bias=handwriting_bias,
            source_type=source_type,
        )
        boxes_per_page.append(boxes)
        regions_per_page.append(regions)

    # 크로스-페이지 검증: 번호가 있는 페이지들만. OpenCV fallback(번호 無)은 그대로 유지.
    validated_regions = validate_anchors_across_pages(regions_per_page)

    # 드롭된 region의 박스도 함께 제거 (같은 인덱스).
    # boxes_per_page와 regions_per_page를 같은 인덱스로 동기 갱신해야 한다.
    # 과거에는 regions_per_page를 그대로 반환해 다운스트림(_boxes_to_questions /
    # segment_questions_multipage)에서 len(regions) != len(boxes) 가 되어 페이지의
    # 번호가 모두 None으로 폴백되는 손실이 발생했다. 이로 인해 OCR이 정상 검출한
    # 시험지 페이지가 OpenCV fallback처럼 보이며 fallback counter로 잘못된 시험지
    # 번호가 매겨지는 결함이 운영 doc#329 / #294 / #292 / #291 등에서 광범위 발생.
    for page_idx, (original, validated) in enumerate(zip(regions_per_page, validated_regions)):
        if not original or len(original) == len(validated):
            continue  # 변화 없음 or 애초에 번호 없음
        kept_region_ids = {id(r) for r in validated}
        boxes_per_page[page_idx] = [
            box for box, region in zip(boxes_per_page[page_idx], original)
            if id(region) in kept_region_ids
        ]
        box_meta = list(page_infos[page_idx].get("text_box_meta") or [])
        if box_meta:
            page_infos[page_idx]["text_box_meta"] = [
                meta for meta, region in zip(box_meta, original)
                if id(region) in kept_region_ids
            ]
        dropped = len(original) - len(validated)
        logger.info(
            "PDF_CROSS_PAGE_DROP | page=%d | dropped=%d | kept=%d",
            page_idx, dropped, len(validated),
        )

    return page_infos, boxes_per_page, validated_regions, tmp_dir


def segment_questions(image_path: str, *, source_type: Optional[str] = None) -> List[BBox]:
    """
    worker-side segmentation single entrypoint.
    PDF 파일이면 페이지별로 이미지 변환 후 세그멘테이션.
    이미지 파일이면 직접 세그멘테이션.

    PDF의 경우 page render는 함수 내에서 즉시 정리(번호 결과만 필요). 호출자는
    별도 cleanup 불필요.
    """
    if _is_pdf(image_path):
        page_infos, boxes_per_page, _, tmp_dir = _collect_pdf_pages(image_path, source_type=source_type)
        try:
            if not page_infos:
                logger.warning("PDF_SEGMENT_NO_PAGES | path=%s", image_path)
                return []

            all_boxes: List[BBox] = []
            for page_idx, (info, boxes) in enumerate(zip(page_infos, boxes_per_page)):
                logger.info(
                    "PDF_SEGMENT_PAGE | page=%d | boxes=%d | has_text=%s | text_boxes=%d",
                    page_idx, len(boxes), info["has_embedded_text"], len(info["text_boxes"]),
                )
                all_boxes.extend(boxes)

            return all_boxes
        finally:
            cleanup_pdf_seg_tmp_dirs([tmp_dir])

    # 단일 이미지 (학생 시험지 사진 등) — source_type 신호 그대로 전달.
    # has_embedded_text 는 단일 이미지엔 없음 (False) → scan_only 모드에서 CLAHE 활성화.
    return _segment_single_image(
        image_path,
        source_type=source_type,
        has_embedded_text=False,
    )


def segment_questions_multipage(
    image_path: str,
    *,
    source_type: Optional[str] = None,
) -> Dict[str, any]:
    """
    PDF 문항 분할 확장판 — 페이지별 결과 + 전체 이미지 경로 반환.
    question_segmentation 워커에서 사용.

    source_type — 학원장 입력 신호. paper_type 분류기에 handwriting_bias로 전달.
    student_exam_photo면 STUDENT_ANSWER_PHOTO 분기 강제 → pipeline의 page-as-problem
    폴백이 신뢰성 있게 작동.

    Returns:
        {
            "pages": [
                {
                    "page_index": 0,
                    "image_path": str,
                    "boxes": [(x,y,w,h), ...],
                    "numbers": [int|None, ...],  # boxes와 같은 길이. 텍스트/OCR 분리에서 추출된 실제 시험지 문항 번호.
                                                  # OpenCV fallback이거나 단일 이미지면 None 채움.
                    "has_embedded_text": bool,
                },
                ...
            ],
            "total_boxes": int,
            "is_pdf": bool,
            "tmp_dirs": [str, ...],  # 호출자가 cleanup_pdf_seg_tmp_dirs로 정리해야 함
                                      # (페이지 image_path들이 이 디렉터리에 살아 있음)
        }
    """
    if _is_pdf(image_path):
        page_infos, boxes_per_page, regions_per_page, tmp_dir = _collect_pdf_pages(
            image_path, source_type=source_type,
        )
        if not page_infos:
            cleanup_pdf_seg_tmp_dirs([tmp_dir])
            return {"pages": [], "total_boxes": 0, "is_pdf": True, "tmp_dirs": []}

        pages = []
        total = 0
        for idx, (info, boxes, regions) in enumerate(zip(page_infos, boxes_per_page, regions_per_page)):
            # regions는 텍스트/OCR 경로에서 boxes와 같은 순서로 채워짐.
            # OpenCV fallback이면 빈 리스트 → None으로 정렬 길이 맞추기.
            if regions and len(regions) == len(boxes):
                numbers = [int(r.number) for r in regions]
            else:
                numbers = [None] * len(boxes)
            bbox_meta = list(info.get("text_box_meta") or [])
            if len(bbox_meta) != len(boxes):
                bbox_meta = []
            pages.append({
                "page_index": idx,
                "image_path": info["image_path"],
                "boxes": boxes,
                "numbers": numbers,
                "bbox_meta": bbox_meta,
                "has_embedded_text": info["has_embedded_text"],
                # 페이지 단위 폴백 시 표지/해설지/lorem ipsum 페이지 제외용.
                "is_skip_page": bool(info.get("is_skip_page")),
                # paper_type 보존 — _aggregate_paper_types가 distribution 계산에 사용.
                # PDF 텍스트/OCR/OpenCV 경로 모두 _classify_and_record_paper_type으로 채움.
                "paper_type": info.get("paper_type") or "unknown",
                "paper_type_debug": info.get("paper_type_debug") or {},
                "page_text": info.get("page_text") or "",
            })
            total += len(boxes)

        return {"pages": pages, "total_boxes": total, "is_pdf": True, "tmp_dirs": [tmp_dir]}

    # 단일 이미지 — 번호 없음. tmp_dir 없음(원본 image_path 그대로 사용).
    # source_type=student_exam_photo 면 scan_only 게이트가 mild CLAHE 활성화 (Stage 6.3Q).
    boxes = _segment_single_image(
        image_path,
        source_type=source_type,
        has_embedded_text=False,
    )
    # 단일 이미지 경로에서도 paper_type을 분류 — 학생답안지 폰사진이 보통 단일 이미지.
    # source_type=student_exam_photo + handwriting_bias로 STUDENT_ANSWER_PHOTO 강제.
    single_info: Dict = {"image_path": image_path}
    _classify_and_record_paper_type(
        single_info, image_path,
        has_embedded_text=False,
        handwriting_bias=_bias_handwriting_score(source_type),
    )
    return {
        "pages": [{
            "page_index": 0,
            "image_path": image_path,
            "boxes": boxes,
            "numbers": [None] * len(boxes),
            "bbox_meta": [],
            "has_embedded_text": False,
            "paper_type": single_info.get("paper_type") or "unknown",
            "paper_type_debug": single_info.get("paper_type_debug") or {},
        }],
        "total_boxes": len(boxes),
        "is_pdf": False,
        "tmp_dirs": [],
    }
