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
import numpy as np  # type: ignore

from apps.worker.ai_worker.ai.config import AIConfig
from apps.worker.ai_worker.ai.detection.segment_opencv import segment_questions_opencv
from apps.worker.ai_worker.ai.detection.segment_yolo import segment_questions_yolo
from apps.worker.ai_worker.ai.detection.segment_ocr import (
    is_ocr_available,
    segment_questions_ocr,
)

# PDF 200 DPI 렌더링 기준 좌표 변환 (points → pixels)
_PDF_TO_PIXEL_SCALE = 200.0 / 72.0

logger = logging.getLogger(__name__)

BBox = Tuple[int, int, int, int]


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


def _pdf_to_images(pdf_path: str) -> Tuple[List[Dict], str]:
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
    from academy.domain.tools.question_splitter import (
        is_non_question_page,
        split_questions,
        TextBlock as SplitterTextBlock,
    )

    results: List[Dict] = []
    tmp_dir = tempfile.mkdtemp(prefix="pdf-seg-")
    # 즉시 추적 등록 — 이후 PDF 렌더 중 예외가 나도 dispatcher finally가 정리.
    # 호출자가 register_pdf_seg_tmp_dirs를 호출해도 동일 dir 중복 등록은 무해
    # (cleanup은 prefix + 존재 여부 검증 후 rmtree, 동일 dir 두 번 처리해도 ignore_errors).
    register_pdf_seg_tmp_dirs([tmp_dir])

    with PdfDocument(pdf_path) as doc:
        page_count = doc.page_count()
        logger.info("PDF_TO_IMAGES | pages=%d | path=%s", page_count, pdf_path)

        for i in range(page_count):
            pil_img = doc.render_page(i, dpi=200)
            out_path = os.path.join(tmp_dir, f"page_{i:03d}.png")
            pil_img.save(out_path, "PNG")

            # 텍스트 존재 여부 검사 — 스캔본이면 False
            has_text = False
            text_boxes: List[BBox] = []
            try:
                raw_blocks = doc.extract_text_blocks(i)
                has_text = len(raw_blocks) > 0
            except Exception:
                raw_blocks = []

            # 텍스트 PDF의 경우 text-based 분할을 시도해서 per-question 박스 사전 계산.
            # is_non_question_page가 True면 표지/목차/안내/해설 — 이 페이지는 OCR/OpenCV
            # 안전망에서도 problem으로 잘리지 않도록 plain "skip" 플래그를 표시.
            text_regions: List = []  # QuestionRegion in points (for cross-page validation)
            is_skip_page = False
            if has_text:
                try:
                    tbs = [
                        SplitterTextBlock(text=b.text, x0=b.x0, y0=b.y0, x1=b.x1, y1=b.y1)
                        for b in raw_blocks
                    ]
                    if is_non_question_page(tbs):
                        is_skip_page = True
                        logger.info(
                            "PDF_TEXT_NON_QUESTION_PAGE | page=%d | skip=True", i,
                        )
                    else:
                        pw, ph = doc.page_dimensions(i)
                        regions = split_questions(tbs, pw, ph, page_index=i)
                        text_regions = list(regions)
                        scale = _PDF_TO_PIXEL_SCALE
                        for r in regions:
                            rx0, ry0, rx1, ry1 = r.bbox
                            text_boxes.append((
                                int(rx0 * scale),
                                int(ry0 * scale),
                                int((rx1 - rx0) * scale),
                                int((ry1 - ry0) * scale),
                            ))
                except Exception as e:
                    logger.warning(
                        "PDF_TEXT_BOXES_ERROR | page=%d | error=%s", i, e,
                    )

            results.append({
                "image_path": out_path,
                "has_embedded_text": has_text,
                "text_boxes": text_boxes,
                "text_regions": text_regions,  # QuestionRegion[] — aligned with text_boxes
                "is_skip_page": is_skip_page,  # 비문항 페이지 (표지/목차/안내). 안전망 우회 금지.
            })

    return results, tmp_dir


def _segment_single_image(
    image_path: str,
    *,
    skip_ocr: bool = False,
    is_pdf_page: bool = False,
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
    """
    cfg = AIConfig.load()
    engine = (cfg.QUESTION_SEGMENTATION_ENGINE or "auto").lower()

    if engine == "opencv":
        return segment_questions_opencv(image_path)
    if engine == "yolo":
        return segment_questions_yolo(image_path)
    if engine == "ocr":
        return segment_questions_ocr(image_path)

    # auto 모드: YOLO는 PDF 페이지에만 사용 (카메라 사진 오탐 방지)
    if is_pdf_page:
        try:
            boxes = segment_questions_yolo(image_path)
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

    return segment_questions_opencv(image_path)


def _boxes_and_regions_for_pdf_page(
    page_info: Dict, page_index: int,
) -> Tuple[List[BBox], List]:
    """
    PDF 페이지 1개에 대한 최종 박스 + QuestionRegion (번호 포함) 반환.

    regions는 크로스-페이지 anchor 검증에 쓰이며, 번호가 없는
    (OpenCV fallback) 경우 빈 리스트로 반환.

    우선순위:
      1. 텍스트 기반 분할 성공 → text_boxes + text_regions 사용
      2. 스캔본 + OCR 가용 → OCR 결과 (boxes + numbered regions)
      3. OCR 불가 / 예외 → OpenCV 안전망 (번호 없음)
    """
    from academy.domain.tools.question_splitter import QuestionRegion

    if page_info["text_boxes"]:
        return list(page_info["text_boxes"]), list(page_info.get("text_regions") or [])

    # text-PDF에서 is_non_question_page=True로 판정된 페이지는 OCR/OpenCV 안전망에서도
    # problem 박스를 만들지 않음. 표지/목차/안내가 sequential global_number로 잘리는 leak 차단.
    if page_info.get("is_skip_page"):
        return [], []

    image_path = page_info["image_path"]

    # 스캔본에서 OCR 가용 시 — OCR 결과 신뢰
    if not page_info["has_embedded_text"] and is_ocr_available():
        try:
            from apps.worker.ai_worker.ai.detection.segment_ocr import (
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
            return boxes, regions  # 빈 결과도 trust (non-question page)
        except Exception as e:
            logger.warning(
                "PDF_PAGE_OCR_FAIL | path=%s | error=%s",
                image_path, e,
            )
            # fallthrough → OpenCV 안전망

    # 텍스트 있지만 분할 실패 OR OCR 크레덴셜 없음 OR OCR 예외
    skip_ocr = page_info["has_embedded_text"]
    boxes = _segment_single_image(image_path, skip_ocr=skip_ocr, is_pdf_page=True)
    boxes = _filter_cover_like_boxes(boxes, image_path, page_index)
    return boxes, []  # OpenCV fallback — 번호 없음


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


def _collect_pdf_pages(image_path: str) -> Tuple[List[Dict], List[List[BBox]], List[List], str]:
    """
    PDF의 모든 페이지를 처리해서 (page_infos, boxes_per_page, regions_per_page, tmp_dir)를 반환.
    크로스-페이지 anchor 검증을 적용해 spurious/outlier 박스를 제거.

    tmp_dir은 호출자가 cleanup_pdf_seg_tmp_dirs([tmp_dir])로 정리해야 함.
    """
    from academy.domain.tools.question_splitter import validate_anchors_across_pages

    page_infos, tmp_dir = _pdf_to_images(image_path)
    if not page_infos:
        return [], [], [], tmp_dir

    boxes_per_page: List[List[BBox]] = []
    regions_per_page: List[List] = []
    for page_idx, info in enumerate(page_infos):
        boxes, regions = _boxes_and_regions_for_pdf_page(info, page_idx)
        boxes_per_page.append(boxes)
        regions_per_page.append(regions)

    # 크로스-페이지 검증: 번호가 있는 페이지들만. OpenCV fallback(번호 無)은 그대로 유지.
    validated_regions = validate_anchors_across_pages(regions_per_page)

    # 드롭된 region의 박스도 함께 제거 (같은 인덱스).
    for page_idx, (original, validated) in enumerate(zip(regions_per_page, validated_regions)):
        if not original or len(original) == len(validated):
            continue  # 변화 없음 or 애초에 번호 없음
        kept_nums = {r.number for r in validated}
        boxes_per_page[page_idx] = [
            box for box, region in zip(boxes_per_page[page_idx], original)
            if region.number in kept_nums
        ]
        dropped = len(original) - len(validated)
        logger.info(
            "PDF_CROSS_PAGE_DROP | page=%d | dropped=%d | kept=%d",
            page_idx, dropped, len(validated),
        )

    return page_infos, boxes_per_page, regions_per_page, tmp_dir


def segment_questions(image_path: str) -> List[BBox]:
    """
    worker-side segmentation single entrypoint.
    PDF 파일이면 페이지별로 이미지 변환 후 세그멘테이션.
    이미지 파일이면 직접 세그멘테이션.

    PDF의 경우 page render는 함수 내에서 즉시 정리(번호 결과만 필요). 호출자는
    별도 cleanup 불필요.
    """
    if _is_pdf(image_path):
        page_infos, boxes_per_page, _, tmp_dir = _collect_pdf_pages(image_path)
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

    return _segment_single_image(image_path)


def segment_questions_multipage(image_path: str) -> Dict[str, any]:
    """
    PDF 문항 분할 확장판 — 페이지별 결과 + 전체 이미지 경로 반환.
    question_segmentation 워커에서 사용.

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
        page_infos, boxes_per_page, regions_per_page, tmp_dir = _collect_pdf_pages(image_path)
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
            pages.append({
                "page_index": idx,
                "image_path": info["image_path"],
                "boxes": boxes,
                "numbers": numbers,
                "has_embedded_text": info["has_embedded_text"],
            })
            total += len(boxes)

        return {"pages": pages, "total_boxes": total, "is_pdf": True, "tmp_dirs": [tmp_dir]}

    # 단일 이미지 — 번호 없음. tmp_dir 없음(원본 image_path 그대로 사용).
    boxes = _segment_single_image(image_path)
    return {
        "pages": [{
            "page_index": 0,
            "image_path": image_path,
            "boxes": boxes,
            "numbers": [None] * len(boxes),
            "has_embedded_text": False,
        }],
        "total_boxes": len(boxes),
        "is_pdf": False,
        "tmp_dirs": [],
    }
