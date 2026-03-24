# apps/worker/ai_worker/ai/omr/identifier.py
"""
OMR Identifier (phone number) detection engine v9

8-digit identifier extraction from aligned full-page image.

v9 changes:
- Removed internal resize (caller handles resolution)
- Adaptive thresholding (replaces per-bubble OTSU)
- Column normalization via z-score
- Multi-feature scoring: fill_ratio + darkness + uniformity
- Local anchor alignment for v9 meta
- Z-score based confidence calculation
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import cv2  # type: ignore
import numpy as np  # type: ignore

from apps.worker.ai_worker.ai.omr.meta_px import build_page_scale_from_meta, PageScale

logger = logging.getLogger(__name__)

BBox = Tuple[int, int, int, int]


@dataclass(frozen=True)
class IdentifierConfigV1:
    """
    Identifier OMR v9 (8 digits, each digit 0~9 single mark).

    Principles:
    - ROI based multi-feature scoring
    - Column normalization via z-score for robust digit detection
    - Adaptive thresholding for consistent binarization
    - Local anchor alignment for v9 meta
    """
    # ROI 확장 계수: r * k
    roi_expand_k: float = 1.5
    # blank 판단: 해당 digit에서 최고 z-score 기반
    blank_threshold: float = 0.06
    # ambiguous 판단: gap 기준
    conf_gap_threshold: float = 0.04
    # digit-level confidence clamp
    min_confidence: float = 0.0
    max_confidence: float = 1.0
    # v9 features
    use_local_alignment: bool = True
    use_adaptive_threshold: bool = True
    use_column_normalization: bool = True
    use_multi_feature: bool = True
    # z-score thresholds
    min_z_score_ok: float = 2.5
    min_z_score_ambiguous: float = 1.5


def _clamp(v: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, v))


def _crop(gray: np.ndarray, bbox: BBox) -> np.ndarray:
    x, y, w, h = bbox
    x = _clamp(int(x), 0, gray.shape[1] - 1)
    y = _clamp(int(y), 0, gray.shape[0] - 1)
    w = max(1, int(w))
    h = max(1, int(h))
    w = min(w, gray.shape[1] - x)
    h = min(h, gray.shape[0] - y)
    return gray[y:y + h, x:x + w]


def _bubble_roi_bbox_px(
    *,
    center_px: Tuple[int, int],
    r_px: int,
    cfg: IdentifierConfigV1,
    img_w: int,
    img_h: int,
) -> BBox:
    cx, cy = center_px
    side = int(round(max(2, r_px) * cfg.roi_expand_k)) * 2
    x = int(cx - side // 2)
    y = int(cy - side // 2)
    x = _clamp(x, 0, img_w - 1)
    y = _clamp(y, 0, img_h - 1)
    w = _clamp(side, 1, img_w - x)
    h = _clamp(side, 1, img_h - y)
    return (x, y, w, h)


def _compute_multi_feature_score(
    gray_roi: np.ndarray,
    binary_roi: np.ndarray,
    use_multi_feature: bool = True,
) -> Tuple[float, Dict[str, float]]:
    """
    Compute multi-feature score for a single bubble ROI.

    Returns (score, details_dict).
    """
    if gray_roi.size == 0 or binary_roi.size == 0:
        return 0.0, {"fill_ratio": 0.0, "darkness": 0.0, "uniformity": 0.0}

    roi_h, roi_w = binary_roi.shape[:2]

    # Elliptical mask
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

    if not use_multi_feature:
        return fill_ratio, {"fill_ratio": fill_ratio, "darkness": 0.0, "uniformity": 0.0}

    # Darkness & uniformity on center 60%
    margin_x = int(roi_w * 0.2)
    margin_y = int(roi_h * 0.2)
    center_roi = gray_roi[margin_y:roi_h - margin_y, margin_x:roi_w - margin_x]
    if center_roi.size == 0:
        center_roi = gray_roi

    mean_intensity = float(np.mean(center_roi))
    std_intensity = float(np.std(center_roi))

    darkness = 1.0 - (mean_intensity / 255.0)
    uniformity = max(0.0, min(1.0, 1.0 - (std_intensity / 128.0)))

    score = 0.5 * fill_ratio + 0.3 * darkness + 0.2 * uniformity

    return score, {
        "fill_ratio": round(fill_ratio, 4),
        "darkness": round(darkness, 4),
        "uniformity": round(uniformity, 4),
    }


def _detect_id_anchors(
    gray: np.ndarray,
    scale: PageScale,
    anchors_meta: Dict[str, Any],
) -> Optional[np.ndarray]:
    """
    Detect identifier anchor squares and compute local affine transform.
    Returns 2x3 affine matrix or None.
    """
    top_anchor = anchors_meta.get("top")
    bottom_anchor = anchors_meta.get("bottom")
    if not top_anchor or not bottom_anchor:
        return None

    try:
        top_exp_x, top_exp_y = scale.mm_to_px_point(
            float(top_anchor["x"]), float(top_anchor["y"])
        )
        bot_exp_x, bot_exp_y = scale.mm_to_px_point(
            float(bottom_anchor["x"]), float(bottom_anchor["y"])
        )

        top_det = _detect_anchor_square(gray, top_exp_x, top_exp_y, scale)
        bot_det = _detect_anchor_square(gray, bot_exp_x, bot_exp_y, scale)

        if top_det is None or bot_det is None:
            return None

        dx_top = top_det[0] - top_exp_x
        dy_top = top_det[1] - top_exp_y
        dx_bot = bot_det[0] - bot_exp_x
        dy_bot = bot_det[1] - bot_exp_y

        src_pts = np.array([
            [top_exp_x, top_exp_y],
            [bot_exp_x, bot_exp_y],
            [top_exp_x + 100, top_exp_y],
        ], dtype=np.float32)

        dst_pts = np.array([
            [top_exp_x + dx_top, top_exp_y + dy_top],
            [bot_exp_x + dx_bot, bot_exp_y + dy_bot],
            [top_exp_x + 100 + dx_top, top_exp_y + dy_top],
        ], dtype=np.float32)

        return cv2.getAffineTransform(src_pts, dst_pts)
    except Exception:
        logger.debug("Identifier anchor detection failed")
        return None


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

    _, thresh = cv2.threshold(roi, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

    contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None

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


def _apply_affine_point(
    cx: int, cy: int, M: Optional[np.ndarray],
) -> Tuple[int, int]:
    """Apply affine transform to a point."""
    if M is None:
        return cx, cy
    pt = np.array([cx, cy, 1.0], dtype=np.float64)
    result = M @ pt
    return int(round(result[0])), int(round(result[1]))


def detect_identifier_v1(
    *,
    image_bgr: np.ndarray,
    meta: Dict[str, Any],
    cfg: Optional[IdentifierConfigV1] = None,
) -> Dict[str, Any]:
    """
    Extract identifier(8 digits) from aligned full-page image.

    meta requirements:
      meta["identifier"]["bubbles"] list with:
        - digit_index (1..8)
        - number (0..9)
        - center: {x(mm), y(mm)}
        - r(mm)

    return contract:
      {
        "identifier": "12345678" | None,
        "digits": [{"digit_index":1,"value":1,"status":"ok|blank|ambiguous","confidence":0.91,"marks":[...]}...],
        "confidence": 0.0~1.0,
        "status": "ok|ambiguous|blank|error"
      }
    """
    cfg = cfg or IdentifierConfigV1()

    if image_bgr is None or image_bgr.size == 0:
        return {"identifier": None, "digits": [], "confidence": 0.0, "status": "error"}

    h, w = image_bgr.shape[:2]
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)

    ident = meta.get("identifier") or {}
    # v7: identifier.digits[].bubbles[], 구버전: identifier.bubbles[]
    raw_bubbles = list(ident.get("bubbles") or [])
    digits_meta = list(ident.get("digits") or [])

    # v7 형식을 flat bubbles 리스트로 변환
    if digits_meta and not raw_bubbles:
        for dm in digits_meta:
            di = dm.get("digit_index", 0)
            for bub in dm.get("bubbles", []):
                raw_bubbles.append({
                    "digit_index": di,
                    "number": int(bub.get("value", bub.get("number", 0))),
                    "center": bub.get("center", {}),
                    "r": float(bub.get("r", 0) or max(
                        float(bub.get("radius_x", 0) or 0),
                        float(bub.get("radius_y", 0) or 0),
                    )),
                })

    if not raw_bubbles:
        return {"identifier": None, "digits": [], "confidence": 0.0, "status": "error"}

    scale: PageScale = build_page_scale_from_meta(meta=meta, image_size_px=(w, h))

    # --- Local anchor alignment (v9 only) ---
    id_affine: Optional[np.ndarray] = None
    meta_version = meta.get("version", "v8")
    if meta_version == "v9" and cfg.use_local_alignment:
        anchors_meta = ident.get("anchors", {})
        if anchors_meta:
            id_affine = _detect_id_anchors(gray, scale, anchors_meta)

    # --- Adaptive threshold on identifier region ---
    if cfg.use_adaptive_threshold:
        blur = cv2.GaussianBlur(gray, (5, 5), 0)
        id_binary = cv2.adaptiveThreshold(
            blur, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY_INV,
            blockSize=15, C=8,
        )
    else:
        id_binary = None  # Will use per-bubble OTSU fallback

    # group bubbles by digit_index
    by_digit: Dict[int, List[Dict[str, Any]]] = {}
    for b in raw_bubbles:
        try:
            di = int(b.get("digit_index") or 0)
        except Exception:
            continue
        if di < 0:
            continue
        by_digit.setdefault(di, []).append(b)

    digits_out: List[Dict[str, Any]] = []
    identifier_chars: List[str] = []
    status_rollup = "ok"
    confidences: List[float] = []

    for digit_index in sorted(by_digit.keys()):
        bs = by_digit[digit_index]

        marks: List[Dict[str, Any]] = []
        raw_scores: List[float] = []

        for b in bs:
            num = int(b.get("number") or b.get("value") or 0)
            c = b.get("center") or {}
            r_mm = float(b.get("r") or max(
                float(b.get("radius_x") or 0),
                float(b.get("radius_y") or 0),
            ) or 1.8)

            cx_mm = float(c.get("x") or 0.0)
            cy_mm = float(c.get("y") or 0.0)

            cx_px, cy_px = scale.mm_to_px_point(cx_mm, cy_mm)

            # Apply local anchor alignment (v9)
            if id_affine is not None:
                cx_px, cy_px = _apply_affine_point(cx_px, cy_px, id_affine)
                cx_px = max(0, min(w - 1, cx_px))
                cy_px = max(0, min(h - 1, cy_px))

            # radius: use average scale for robustness (mm->px)
            r_px_x = max(1, scale.mm_to_px_len_x(r_mm))
            r_px_y = max(1, scale.mm_to_px_len_y(r_mm))
            r_px = max(1, int(round((r_px_x + r_px_y) / 2.0)))

            bbox = _bubble_roi_bbox_px(
                center_px=(cx_px, cy_px),
                r_px=r_px,
                cfg=cfg,
                img_w=w,
                img_h=h,
            )

            # Compute score
            if id_binary is not None and cfg.use_multi_feature:
                gray_roi = _crop(gray, bbox)
                binary_roi = _crop(id_binary, bbox)
                score, details = _compute_multi_feature_score(
                    gray_roi, binary_roi, use_multi_feature=cfg.use_multi_feature,
                )
            elif id_binary is not None:
                # Adaptive threshold but no multi-feature
                binary_roi = _crop(id_binary, bbox)
                filled = float(np.count_nonzero(binary_roi))
                total = float(binary_roi.size) if binary_roi.size > 0 else 1.0
                score = filled / total
                details = {"fill_ratio": round(score, 4)}
            else:
                # Legacy per-bubble OTSU fallback
                roi = _crop(gray, bbox)
                score = _fill_score_legacy(roi)
                details = {"fill_ratio": round(score, 4)}

            raw_scores.append(score)
            marks.append(
                {
                    "number": int(num),
                    "fill": float(score),
                    "details": details,
                    "center_px": {"x": int(cx_px), "y": int(cy_px)},
                    "roi_px": {"x": int(bbox[0]), "y": int(bbox[1]),
                               "w": int(bbox[2]), "h": int(bbox[3])},
                }
            )

        # --- Column normalization (v9) ---
        if cfg.use_column_normalization and len(raw_scores) >= 3:
            fills_array = np.array(raw_scores)
            baseline = float(np.median(fills_array))
            std = float(np.std(fills_array)) + 1e-6
            z_scores = [(s - baseline) / std for s in raw_scores]

            # Update marks with z-scores
            for i, m in enumerate(marks):
                m["z_score"] = round(z_scores[i], 4)

            # Sort by z-score for column-normalized decision
            marks_sorted = sorted(marks, key=lambda m: float(m.get("z_score", 0.0)), reverse=True)
            top = marks_sorted[0]
            second = marks_sorted[1] if len(marks_sorted) > 1 else {"number": 0, "z_score": -999.0}

            top_z = float(top.get("z_score", 0.0))
            second_z = float(second.get("z_score", 0.0))
            z_gap = top_z - second_z

            # Confidence from z-score: z=5 -> conf=1.0
            conf = min(1.0, max(0.0, top_z / 5.0))

            if top_z < cfg.min_z_score_ambiguous:
                # Likely blank
                digits_out.append({
                    "digit_index": int(digit_index),
                    "value": None,
                    "status": "blank",
                    "confidence": 0.0,
                    "z_score": round(top_z, 4),
                    "marks": marks_sorted,
                })
                identifier_chars.append("?")
                status_rollup = "blank" if status_rollup == "ok" else status_rollup
                continue

            if top_z < cfg.min_z_score_ok or z_gap < 1.5 * std:
                # Ambiguous
                digits_out.append({
                    "digit_index": int(digit_index),
                    "value": int(top.get("number") or 0),
                    "status": "ambiguous",
                    "confidence": float(max(cfg.min_confidence, min(cfg.max_confidence, conf))),
                    "z_score": round(top_z, 4),
                    "z_gap": round(z_gap, 4),
                    "marks": marks_sorted,
                })
                identifier_chars.append(str(int(top.get("number") or 0)))
                status_rollup = "ambiguous" if status_rollup in ("ok",) else status_rollup
                confidences.append(float(conf))
                continue

            # OK
            digits_out.append({
                "digit_index": int(digit_index),
                "value": int(top.get("number") or 0),
                "status": "ok",
                "confidence": float(max(cfg.min_confidence, min(cfg.max_confidence, conf))),
                "z_score": round(top_z, 4),
                "z_gap": round(z_gap, 4),
                "marks": marks_sorted,
            })
            identifier_chars.append(str(int(top.get("number") or 0)))
            confidences.append(float(conf))

        else:
            # Legacy fill-based decision (no column normalization)
            marks_sorted = sorted(marks, key=lambda m: float(m.get("fill") or 0.0), reverse=True)
            top = marks_sorted[0] if marks_sorted else {"number": 0, "fill": 0.0}
            second = marks_sorted[1] if len(marks_sorted) > 1 else {"number": 0, "fill": 0.0}

            top_fill = float(top.get("fill") or 0.0)
            second_fill = float(second.get("fill") or 0.0)
            gap = float(top_fill - second_fill)

            if top_fill < cfg.blank_threshold:
                digits_out.append({
                    "digit_index": int(digit_index),
                    "value": None,
                    "status": "blank",
                    "confidence": 0.0,
                    "marks": marks_sorted,
                })
                identifier_chars.append("?")
                status_rollup = "blank" if status_rollup == "ok" else status_rollup
                continue

            if gap < cfg.conf_gap_threshold:
                digits_out.append({
                    "digit_index": int(digit_index),
                    "value": int(top.get("number") or 0),
                    "status": "ambiguous",
                    "confidence": float(max(cfg.min_confidence, min(cfg.max_confidence, top_fill))),
                    "gap": float(gap),
                    "marks": marks_sorted,
                })
                identifier_chars.append(str(int(top.get("number") or 0)))
                status_rollup = "ambiguous" if status_rollup in ("ok",) else status_rollup
                confidences.append(float(top_fill))
                continue

            # ok
            conf = float(max(cfg.min_confidence, min(cfg.max_confidence, top_fill)))
            digits_out.append({
                "digit_index": int(digit_index),
                "value": int(top.get("number") or 0),
                "status": "ok",
                "confidence": conf,
                "gap": float(gap),
                "marks": marks_sorted,
            })
            identifier_chars.append(str(int(top.get("number") or 0)))
            confidences.append(conf)

    # identifier validity: must be 8 digits all ok/ambiguous (blank이면 ? 포함)
    identifier = "".join(identifier_chars)
    if "?" in identifier:
        identifier_final: Optional[str] = None
    else:
        identifier_final = identifier

    # overall confidence: conservative (mean of digit conf where available)
    overall_conf = float(sum(confidences) / len(confidences)) if confidences else 0.0

    return {
        "identifier": identifier_final,
        "raw_identifier": identifier,  # '?' 포함 가능 (운영 디버그/리트라이용)
        "digits": digits_out,
        "confidence": float(max(0.0, min(1.0, overall_conf))),
        "status": status_rollup,
    }


def _fill_score_legacy(roi_gray: np.ndarray) -> float:
    """
    Legacy per-bubble OTSU fill score (fallback when adaptive threshold disabled).
    """
    if roi_gray.size == 0:
        return 0.0

    blur = cv2.GaussianBlur(roi_gray, (5, 5), 0)
    _, th = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

    filled = float(np.sum(th > 0))
    total = float(th.size) if th.size > 0 else 1.0
    score = filled / total
    return float(max(0.0, min(1.0, score)))
