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
    roi_expand_k: float = 1.5
    # blank 판단: 최고 score가 이 값 미만이면 blank
    blank_threshold: float = 0.08
    # ambiguous 판단: top-2 gap이 이 값 미만이면 ambiguous
    conf_gap_threshold: float = 0.08
    # adaptive threshold 사용 여부
    use_adaptive_threshold: bool = True
    # adaptive threshold block size (must be odd)
    adaptive_block_size: int = 15
    # adaptive threshold C constant
    adaptive_c: int = 8
    # elliptical mask 사용 여부
    use_elliptical_mask: bool = True
    # multi-feature scoring 사용 여부
    use_multi_feature: bool = True


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

    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)

    # --- Binarization ---
    if config.use_adaptive_threshold:
        blur = cv2.GaussianBlur(gray, (5, 5), 0)
        binary = cv2.adaptiveThreshold(
            blur, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY_INV,
            blockSize=config.adaptive_block_size,
            C=config.adaptive_c,
        )
    else:
        # Legacy fixed threshold fallback
        _, binary = cv2.threshold(gray, 140, 255, cv2.THRESH_BINARY_INV)

    # --- Column-local alignment (v9 only) ---
    col_transforms: Dict[int, np.ndarray] = {}
    meta_version = meta.get("version", "v8")
    if meta_version == "v9" and meta.get("columns"):
        col_transforms = _compute_column_transforms(
            gray=gray, scale=scale, columns_meta=meta["columns"],
        )

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
                version="v9",
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
            top_exp_x, top_exp_y = scale.mm_to_px_point(
                float(top_anchor["x"]), float(top_anchor["y"])
            )
            bot_exp_x, bot_exp_y = scale.mm_to_px_point(
                float(bottom_anchor["x"]), float(bottom_anchor["y"])
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

    # --- Fill ratio ---
    if config.use_elliptical_mask:
        mask = np.zeros((roi_h, roi_w), dtype=np.uint8)
        cv2.ellipse(
            mask,
            (roi_w // 2, roi_h // 2),
            (roi_w // 2, roi_h // 2),
            0, 0, 360, 255, -1,
        )
        filled_pixels = np.count_nonzero(cv2.bitwise_and(binary_roi, mask))
        total_pixels = np.count_nonzero(mask)
        fill_ratio = float(filled_pixels) / max(1, total_pixels)
    else:
        fill_ratio = float(np.count_nonzero(binary_roi)) / max(1, binary_roi.size)

    if not config.use_multi_feature:
        return fill_ratio, {"fill_ratio": fill_ratio, "darkness": 0.0, "uniformity": 0.0}

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

    # Composite score: weighted combination
    score = 0.5 * fill_ratio + 0.3 * darkness + 0.2 * uniformity

    return score, {
        "fill_ratio": round(fill_ratio, 4),
        "darkness": round(darkness, 4),
        "uniformity": round(uniformity, 4),
    }


def _detect_single_question(
    *,
    gray: np.ndarray,
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

    if not fills:
        return OMRAnswerV1(
            version="v9", question_id=q_num,
            detected=[], marking="blank",
            confidence=0.0, status="error",
        )

    # 정렬: score 높은 순
    fills.sort(key=lambda x: x[1], reverse=True)
    top_label, top_score, top_details = fills[0]
    second_score = fills[1][1] if len(fills) > 1 else 0.0
    gap = top_score - second_score

    raw_data = {
        "fills": {l: round(s, 4) for l, s, _ in fills},
        "details": {l: d for l, _, d in fills},
    }

    # 판정
    if top_score < config.blank_threshold:
        return OMRAnswerV1(
            version="v9", question_id=q_num,
            detected=[], marking="blank",
            confidence=0.0, status="blank",
            raw=raw_data,
        )

    if gap < config.conf_gap_threshold:
        # 복수 마킹 가능성
        marked = [l for l, s, _ in fills if s >= config.blank_threshold]
        return OMRAnswerV1(
            version="v9", question_id=q_num,
            detected=marked,
            marking="multi" if len(marked) > 1 else "single",
            confidence=round(gap, 4),
            status="ambiguous",
            raw=raw_data,
        )

    confidence = min(1.0, gap / 0.3)  # gap 0.3 이상이면 confidence 1.0

    return OMRAnswerV1(
        version="v9", question_id=q_num,
        detected=[top_label],
        marking="single",
        confidence=round(confidence, 4),
        status="ok",
        raw=raw_data,
    )
