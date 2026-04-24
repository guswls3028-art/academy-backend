# apps/worker/ai_worker/ai/omr/engine.py
"""
OMR 객관식 답안 검출 엔진 v9

omr-sheet.html SSOT 레이아웃 기준.
meta_generator.py의 좌표를 사용하여 스캔 이미지에서 마킹된 버블을 감지한다.

v9 changes:
- Adaptive thresholding (replaces fixed threshold)
- Elliptical ROI masking (matches bubble shape)
- Multi-feature scoring: fill_ratio + darkness + uniformity
- Column-local alignment via anchors (v9 meta only)
- Backward compatible with v8 meta (skips local alignment)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import cv2  # type: ignore
import numpy as np  # type: ignore

from apps.worker.ai_worker.ai.omr.meta_px import build_page_scale_from_meta, PageScale
from apps.worker.ai_worker.ai.omr.types import OMRAnswerV1

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AnswerDetectConfig:
    """객관식 버블 감지 설정."""
    # ROI 확장 계수 (버블 반지름 x k)
    roi_expand_k: float = 1.2
    # blank 판단: 최고 score가 이 값 미만이면 blank
    blank_threshold: float = 0.08
    # ambiguous 판단: top-2 gap이 이 값 미만이면 ambiguous
    conf_gap_threshold: float = 0.08
    # adaptive threshold 사용 여부
    use_adaptive_threshold: bool = True
    # adaptive threshold block size (must be odd)
    adaptive_block_size: int = 15
    # adaptive threshold C constant (낮을수록 연한 마킹 감지 가능, 너무 낮으면 노이즈 증가)
    adaptive_c: int = 5
    # elliptical mask 사용 여부
    use_elliptical_mask: bool = True
    # multi-feature scoring 사용 여부
    use_multi_feature: bool = True
    # v10: CLAHE 전처리 (그림자/조명 불균일 보정)
    use_clahe: bool = True
    clahe_clip_limit: float = 2.0
    clahe_grid_size: int = 8
    # v10: 이미지 품질 기반 adaptive 파라미터 자동 조정
    use_quality_adaptive: bool = True
    # v10: border density 특성 (부분 마킹/오염 구분)
    use_border_density: bool = True


def detect_omr_answers_v7(
    *,
    image_bgr: np.ndarray,
    meta: Dict[str, Any],
    config: Optional[AnswerDetectConfig] = None,
) -> List[OMRAnswerV1]:
    """
    워프된 A4 이미지에서 객관식 답안을 감지한다.

    Args:
        image_bgr: 워프된 BGR 이미지 (전체 페이지 = 전체 이미지)
        meta: build_omr_meta() 결과
        config: 감지 설정

    Returns:
        문항별 OMRAnswerV1 리스트
    """
    if config is None:
        config = AnswerDetectConfig()

    scale = build_page_scale_from_meta(
        meta=meta,
        image_size_px=(image_bgr.shape[1], image_bgr.shape[0]),
    )

    gray_raw = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    gray = gray_raw.copy()

    # --- v10: CLAHE 전처리 (그림자/조명 불균일 보정) ---
    if config.use_clahe:
        clahe = cv2.createCLAHE(
            clipLimit=config.clahe_clip_limit,
            tileGridSize=(config.clahe_grid_size, config.clahe_grid_size),
        )
        gray = clahe.apply(gray)

    # --- v10: 이미지 품질 기반 파라미터 자동 조정 ---
    block_size = config.adaptive_block_size
    adaptive_c = config.adaptive_c
    if config.use_quality_adaptive:
        blur_metric = cv2.Laplacian(gray, cv2.CV_64F).var()
        if blur_metric < 100:
            # 흐릿한 이미지: 블록 크기 키우고 C 낮춤
            block_size = max(block_size, 21)
            adaptive_c = max(2, adaptive_c - 2)
            logger.info("OMR quality: blurry (laplacian_var=%.1f), block=%d C=%d", blur_metric, block_size, adaptive_c)
        elif blur_metric > 2000:
            # 매우 선명: 기본값 유지
            pass

    # --- Binarization ---
    if config.use_adaptive_threshold:
        blur = cv2.GaussianBlur(gray, (5, 5), 0)
        binary = cv2.adaptiveThreshold(
            blur, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY_INV,
            blockSize=block_size,
            C=adaptive_c,
        )
    else:
        # Legacy fixed threshold fallback
        _, binary = cv2.threshold(gray, 140, 255, cv2.THRESH_BINARY_INV)

    # --- Column-local alignment (v9~v14, displacement-gated) ---
    # v15: 컬럼 앵커 원 제거됨 — 코너 마커 homography만으로 정렬. col_transforms는 빈 dict.
    col_transforms: Dict[int, np.ndarray] = {}
    meta_version = meta.get("version", "v8")
    if meta_version in ("v9", "v10", "v11", "v12", "v13", "v14") and meta.get("columns"):
        raw_transforms = _compute_column_transforms(
            gray=gray, scale=scale, columns_meta=meta["columns"],
        )
        # 잘못된 앵커 감지로 인한 과도한 displacement 필터링.
        # marker_homography가 이미 정밀 워핑을 수행하면 column transform이
        # 오히려 좌표를 어긋나게 함. 최대 5px 이내만 허용.
        _MAX_COL_DISPLACEMENT_PX = 5.0
        for ci, M in raw_transforms.items():
            # displacement = M @ [0,0,1] 의 translation 성분
            dx, dy = abs(M[0, 2]), abs(M[1, 2])
            if dx <= _MAX_COL_DISPLACEMENT_PX and dy <= _MAX_COL_DISPLACEMENT_PX:
                col_transforms[ci] = M
            else:
                logger.debug("Column %d transform rejected: dx=%.1f dy=%.1f exceeds threshold", ci, dx, dy)

    results: List[OMRAnswerV1] = []

    for q in meta.get("questions", []):
        q_num = int(q.get("question_number", 0))
        choices = q.get("choices", [])
        if not choices:
            continue

        # Determine which column this question belongs to (for local alignment)
        col_idx = int(q.get("column", -1))

        try:
            answer = _detect_single_question(
                gray=gray,
                gray_raw=gray_raw,
                binary=binary,
                scale=scale,
                q_num=q_num,
                choices=choices,
                config=config,
                img_shape=image_bgr.shape,
                col_transform=col_transforms.get(col_idx),
                meta_version=meta_version,
            )
            results.append(answer)
        except Exception:
            logger.exception("OMR detect error q=%d", q_num)
            results.append(OMRAnswerV1(
                version=meta_version,
                question_id=q_num,
                detected=[],
                marking="blank",
                confidence=0.0,
                status="error",
            ))

    return results


def _compute_column_transforms(
    *,
    gray: np.ndarray,
    scale: PageScale,
    columns_meta: List[Dict[str, Any]],
) -> Dict[int, np.ndarray]:
    """
    v9 column-local alignment: detect column anchor squares and compute
    per-column affine correction matrix.

    Returns dict mapping column_index -> 2x3 affine transform matrix.
    If anchor detection fails for a column, that column is omitted (no correction).
    """
    transforms: Dict[int, np.ndarray] = {}

    for col_meta in columns_meta:
        col_idx = int(col_meta.get("column_index", -1))
        anchors = col_meta.get("anchors", {})
        if not anchors:
            continue

        # Expect anchors with "top" and "bottom" each having {x, y} in mm
        top_anchor = anchors.get("top")
        bottom_anchor = anchors.get("bottom")
        if not top_anchor or not bottom_anchor:
            continue

        try:
            # Expected anchor positions (from meta, mm -> px)
            top_c = top_anchor.get("center", top_anchor)
            bot_c = bottom_anchor.get("center", bottom_anchor)
            top_exp_x, top_exp_y = scale.mm_to_px_point(
                float(top_c["x"]), float(top_c["y"])
            )
            bot_exp_x, bot_exp_y = scale.mm_to_px_point(
                float(bot_c["x"]), float(bot_c["y"])
            )

            # Detect actual anchor positions in the image
            top_det = _detect_anchor_square(gray, top_exp_x, top_exp_y, scale)
            bot_det = _detect_anchor_square(gray, bot_exp_x, bot_exp_y, scale)

            if top_det is None or bot_det is None:
                continue

            # Build affine from expected -> detected shift
            # Use 3-point affine: top anchor, bottom anchor, and a midpoint offset
            src_pts = np.array([
                [top_exp_x, top_exp_y],
                [bot_exp_x, bot_exp_y],
                [top_exp_x + 100, top_exp_y],  # synthetic third point
            ], dtype=np.float32)

            dx_top = top_det[0] - top_exp_x
            dy_top = top_det[1] - top_exp_y
            dx_bot = bot_det[0] - bot_exp_x
            dy_bot = bot_det[1] - bot_exp_y

            dst_pts = np.array([
                [top_exp_x + dx_top, top_exp_y + dy_top],
                [bot_exp_x + dx_bot, bot_exp_y + dy_bot],
                [top_exp_x + 100 + dx_top, top_exp_y + dy_top],
            ], dtype=np.float32)

            M = cv2.getAffineTransform(src_pts, dst_pts)
            transforms[col_idx] = M
        except Exception:
            logger.debug("Column %d anchor detection failed", col_idx)
            continue

    return transforms


def _detect_anchor_square(
    gray: np.ndarray,
    expected_x: int,
    expected_y: int,
    scale: PageScale,
    search_radius_mm: float = 5.0,
) -> Optional[Tuple[int, int]]:
    """
    Detect a filled anchor square near the expected position.
    Returns (cx, cy) in pixels, or None if not found.
    """
    r_x = max(10, scale.mm_to_px_len_x(search_radius_mm))
    r_y = max(10, scale.mm_to_px_len_y(search_radius_mm))

    img_h, img_w = gray.shape[:2]
    x1 = max(0, expected_x - r_x)
    y1 = max(0, expected_y - r_y)
    x2 = min(img_w, expected_x + r_x)
    y2 = min(img_h, expected_y + r_y)

    roi = gray[y1:y2, x1:x2]
    if roi.size == 0:
        return None

    # Threshold to find dark regions
    _, thresh = cv2.threshold(roi, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

    contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None

    # Find the most square-like contour near center
    best = None
    best_dist = float("inf")
    roi_cx = (x2 - x1) // 2
    roi_cy = (y2 - y1) // 2

    for cnt in contours:
        area = cv2.contourArea(cnt)
        if area < 20:
            continue
        x, y, w, h = cv2.boundingRect(cnt)
        if w == 0 or h == 0:
            continue
        aspect = min(w, h) / max(w, h)
        if aspect < 0.5:
            continue
        cx = x + w // 2
        cy = y + h // 2
        dist = ((cx - roi_cx) ** 2 + (cy - roi_cy) ** 2) ** 0.5
        if dist < best_dist:
            best_dist = dist
            best = (x1 + cx, y1 + cy)

    return best


def _apply_col_transform(
    cx_px: int, cy_px: int, col_transform: Optional[np.ndarray],
) -> Tuple[int, int]:
    """Apply column affine transform to adjust bubble center coordinates."""
    if col_transform is None:
        return cx_px, cy_px
    pt = np.array([cx_px, cy_px, 1.0], dtype=np.float64)
    result = col_transform @ pt
    return int(round(result[0])), int(round(result[1]))


def _compute_bubble_score(
    *,
    gray: np.ndarray,
    gray_raw: np.ndarray,
    binary: np.ndarray,
    cx_px: int,
    cy_px: int,
    rx_px: int,
    ry_px: int,
    img_h: int,
    img_w: int,
    config: AnswerDetectConfig,
) -> Tuple[float, Dict[str, float]]:
    """
    Compute multi-feature score for a single bubble.

    Returns:
        (score, details_dict) where score is 0.0~1.0 composite,
        details_dict has fill_ratio, darkness, uniformity.
    """
    x1 = max(0, cx_px - rx_px)
    y1 = max(0, cy_px - ry_px)
    x2 = min(img_w, cx_px + rx_px)
    y2 = min(img_h, cy_px + ry_px)

    roi_h = y2 - y1
    roi_w = x2 - x1

    if roi_h <= 0 or roi_w <= 0:
        return 0.0, {"fill_ratio": 0.0, "darkness": 0.0, "uniformity": 0.0}

    binary_roi = binary[y1:y2, x1:x2]
    gray_roi = gray[y1:y2, x1:x2]

    # --- Elliptical mask (fill_ratio + raw_darkness 공용) ---
    mask: Optional[np.ndarray] = None
    if config.use_elliptical_mask:
        mask = np.zeros((roi_h, roi_w), dtype=np.uint8)
        cv2.ellipse(
            mask,
            (roi_w // 2, roi_h // 2),
            (roi_w // 2, roi_h // 2),
            0, 0, 360, 255, -1,
        )

    # --- Fill ratio ---
    if mask is not None:
        filled_pixels = np.count_nonzero(cv2.bitwise_and(binary_roi, mask))
        total_pixels = np.count_nonzero(mask)
        fill_ratio = float(filled_pixels) / max(1, total_pixels)
    else:
        fill_ratio = float(np.count_nonzero(binary_roi)) / max(1, binary_roi.size)

    if not config.use_multi_feature:
        return fill_ratio, {"fill_ratio": fill_ratio, "darkness": 0.0, "uniformity": 0.0, "border_density": 0.0, "raw_darkness": 0.0}

    # --- Darkness & Uniformity (center 60% area) ---
    margin_x = int(roi_w * 0.2)
    margin_y = int(roi_h * 0.2)
    center_roi = gray_roi[margin_y:roi_h - margin_y, margin_x:roi_w - margin_x]

    if center_roi.size == 0:
        center_roi = gray_roi  # fallback to full ROI

    mean_intensity = float(np.mean(center_roi))
    std_intensity = float(np.std(center_roi))

    darkness = 1.0 - (mean_intensity / 255.0)
    uniformity = 1.0 - (std_intensity / 128.0)
    uniformity = max(0.0, min(1.0, uniformity))

    # --- v11: Raw darkness (CLAHE/adaptive 영향 없는 원본 기반) ---
    # CLAHE가 지역적으로 명암을 평탄화하면 darkness가 왜곡됨.
    # 원본 gray의 평균 밝기로 마킹 여부를 가장 안정적으로 판별.
    raw_roi = gray_raw[y1:y2, x1:x2]
    if mask is not None and raw_roi.shape == mask.shape:
        raw_masked = cv2.bitwise_and(raw_roi, mask)
        raw_pixels = raw_masked[mask > 0]
    else:
        raw_pixels = raw_roi.flatten()
    raw_mean = float(np.mean(raw_pixels)) if raw_pixels.size > 0 else 255.0
    raw_darkness = 1.0 - (raw_mean / 255.0)

    # --- v10: Border density (부분 마킹/오염 구분) ---
    border_density = 0.0
    if config.use_border_density and roi_h > 4 and roi_w > 4:
        center_binary = binary_roi[margin_y:roi_h - margin_y, margin_x:roi_w - margin_x]
        center_fill = float(np.count_nonzero(center_binary)) / max(1, center_binary.size)
        border_density = min(1.0, center_fill / max(0.01, fill_ratio))

    # Composite score: raw_darkness를 primary feature로 사용
    # raw_darkness는 CLAHE/adaptive 영향을 받지 않아 페이지 전체에서 일관적.
    if config.use_border_density:
        score = 0.15 * fill_ratio + 0.15 * darkness + 0.10 * uniformity + 0.10 * border_density + 0.50 * raw_darkness
    else:
        score = 0.20 * fill_ratio + 0.15 * darkness + 0.15 * uniformity + 0.50 * raw_darkness

    return score, {
        "fill_ratio": round(fill_ratio, 4),
        "darkness": round(darkness, 4),
        "uniformity": round(uniformity, 4),
        "border_density": round(border_density, 4),
        "raw_darkness": round(raw_darkness, 4),
    }


def _detect_single_question(
    *,
    gray: np.ndarray,
    gray_raw: np.ndarray,
    binary: np.ndarray,
    scale: PageScale,
    q_num: int,
    choices: List[Dict[str, Any]],
    config: AnswerDetectConfig,
    img_shape: Tuple[int, ...],
    col_transform: Optional[np.ndarray] = None,
    meta_version: str = "v8",
) -> OMRAnswerV1:
    """단일 문항의 버블 multi-feature scoring 및 판정."""
    img_h, img_w = img_shape[:2]
    fills: List[Tuple[str, float, Dict[str, float]]] = []
    # v10.1: 각 버블의 픽셀 좌표 기록 (검토 UI의 BBox overlay용). 판정 로직에는 영향 없음.
    bubble_rects: List[Dict[str, int]] = []

    for ch in choices:
        label = str(ch.get("label", ""))
        center = ch.get("center", {})
        cx_mm = float(center.get("x", 0))
        cy_mm = float(center.get("y", 0))
        rx_mm = float(ch.get("radius_x", 1.8))
        ry_mm = float(ch.get("radius_y", 2.6))

        cx_px, cy_px = scale.mm_to_px_point(cx_mm, cy_mm)

        # Apply column-local affine correction (v9 only)
        if col_transform is not None:
            cx_px, cy_px = _apply_col_transform(cx_px, cy_px, col_transform)
            cx_px = max(0, min(img_w - 1, cx_px))
            cy_px = max(0, min(img_h - 1, cy_px))

        rx_px = max(1, int(round(rx_mm * config.roi_expand_k * scale.sx)))
        ry_px = max(1, int(round(ry_mm * config.roi_expand_k * scale.sy)))

        score, details = _compute_bubble_score(
            gray=gray,
            gray_raw=gray_raw,
            binary=binary,
            cx_px=cx_px,
            cy_px=cy_px,
            rx_px=rx_px,
            ry_px=ry_px,
            img_h=img_h,
            img_w=img_w,
            config=config,
        )
        fills.append((label, score, details))
        # bbox = 원의 외접 사각형 (x, y, w, h)
        bubble_rects.append({
            "label": label,
            "x": int(max(0, cx_px - rx_px)),
            "y": int(max(0, cy_px - ry_px)),
            "w": int(min(img_w, cx_px + rx_px) - max(0, cx_px - rx_px)),
            "h": int(min(img_h, cy_px + ry_px) - max(0, cy_px - ry_px)),
        })

    if not fills:
        return OMRAnswerV1(
            version=meta_version, question_id=q_num,
            detected=[], marking="blank",
            confidence=0.0, status="error",
        )

    # 정렬: score 높은 순
    fills.sort(key=lambda x: x[1], reverse=True)
    top_label, top_score, top_details = fills[0]
    second_score = fills[1][1] if len(fills) > 1 else 0.0
    gap = top_score - second_score

    # 문항 전체 rect = 모든 버블의 bounding box (검토 UI 좌표)
    if bubble_rects:
        min_x = min(br["x"] for br in bubble_rects)
        min_y = min(br["y"] for br in bubble_rects)
        max_x = max(br["x"] + br["w"] for br in bubble_rects)
        max_y = max(br["y"] + br["h"] for br in bubble_rects)
        question_rect: Optional[Dict[str, int]] = {
            "x": int(min_x),
            "y": int(min_y),
            "w": int(max_x - min_x),
            "h": int(max_y - min_y),
        }
    else:
        question_rect = None

    raw_data = {
        "fills": {l: round(s, 4) for l, s, _ in fills},
        "details": {l: d for l, _, d in fills},
        "bubble_rects": bubble_rects,
        "rect": question_rect,
    }

    # ── 판정 (v10 개선: IQR 기반 noise floor) ──
    # 노이즈 바닥을 IQR로 추정하여 동적 threshold 적용.
    scores_arr = np.array([s for _, s, _ in fills])
    q1 = float(np.percentile(scores_arr, 25))
    q3 = float(np.percentile(scores_arr, 75))
    iqr = q3 - q1
    noise_std = iqr / 1.35  # IQR→σ 근사 (정규분포 가정)
    median_score = float(np.median(scores_arr))
    relative_top = top_score - median_score

    # Blank: 최고 점수가 noise floor 대비 유의미하게 높지 않으면 blank
    _REL_BLANK_TH = max(0.04, 2.0 * noise_std)  # 동적 threshold
    is_blank = relative_top < _REL_BLANK_TH and top_score < 0.35

    if is_blank:
        return OMRAnswerV1(
            version=meta_version, question_id=q_num,
            detected=[], marking="blank",
            confidence=0.0, status="blank",
            raw=raw_data,
        )

    if gap < config.conf_gap_threshold:
        # 복수 마킹 가능성 — 상대적 noise floor 기준
        noise_floor = median_score + _REL_BLANK_TH
        marked = [l for l, s, _ in fills if s >= noise_floor]
        return OMRAnswerV1(
            version=meta_version, question_id=q_num,
            detected=marked,
            marking="multi" if len(marked) > 1 else "single",
            confidence=round(gap, 4),
            status="ambiguous",
            raw=raw_data,
        )

    confidence = min(1.0, gap / 0.3)  # gap 0.3 이상이면 confidence 1.0

    return OMRAnswerV1(
        version=meta_version, question_id=q_num,
        detected=[top_label],
        marking="single",
        confidence=round(confidence, 4),
        status="ok",
        raw=raw_data,
    )
