from __future__ import annotations

from typing import Optional, Tuple

import cv2  # type: ignore
import numpy as np  # type: ignore

from academy.adapters.ai.omr.meta_px import PageScale


def detect_filled_anchor_square(
    *,
    gray: np.ndarray,
    expected_x: int,
    expected_y: int,
    scale: PageScale,
    search_radius_mm: float = 12.0,
) -> Optional[Tuple[int, int]]:
    """Detect the solid 2mm-ish local-alignment anchor near an expected point."""
    h, w = gray.shape[:2]
    r_x = max(10, scale.mm_to_px_len_x(search_radius_mm))
    r_y = max(10, scale.mm_to_px_len_y(search_radius_mm))
    x1 = max(0, expected_x - r_x)
    y1 = max(0, expected_y - r_y)
    x2 = min(w, expected_x + r_x)
    y2 = min(h, expected_y + r_y)
    roi = gray[y1:y2, x1:x2]
    if roi.size == 0:
        return None

    _, thresh = cv2.threshold(
        roi,
        0,
        255,
        cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU,
    )
    contours, _ = cv2.findContours(
        thresh,
        cv2.RETR_EXTERNAL,
        cv2.CHAIN_APPROX_SIMPLE,
    )
    if not contours:
        return None

    best = None
    best_score = float("-inf")
    roi_cx = (x2 - x1) // 2
    roi_cy = (y2 - y1) // 2
    expected_side_x = scale.mm_to_px_len_x(2.0)
    expected_side_y = scale.mm_to_px_len_y(2.0)
    min_side_x = max(6, scale.mm_to_px_len_x(1.2))
    min_side_y = max(6, scale.mm_to_px_len_y(1.2))
    max_side_x = max(min_side_x + 4, scale.mm_to_px_len_x(3.4))
    max_side_y = max(min_side_y + 4, scale.mm_to_px_len_y(3.4))

    for cnt in contours:
        area = cv2.contourArea(cnt)
        if area < 20:
            continue
        x, y, bw, bh = cv2.boundingRect(cnt)
        if bw == 0 or bh == 0:
            continue
        if not (min_side_x <= bw <= max_side_x and min_side_y <= bh <= max_side_y):
            continue
        aspect = min(bw, bh) / max(bw, bh)
        if aspect < 0.75:
            continue
        fill_ratio = float(area) / float(bw * bh)
        if fill_ratio < 0.55:
            continue
        hull = cv2.convexHull(cnt)
        hull_area = cv2.contourArea(hull)
        solidity = float(area) / float(hull_area) if hull_area > 0 else 0.0
        if solidity < 0.80:
            continue

        cx = x + bw // 2
        cy = y + bh // 2
        dist = ((cx - roi_cx) ** 2 + (cy - roi_cy) ** 2) ** 0.5
        size_penalty = (
            abs(bw - expected_side_x) / max(1.0, float(expected_side_x))
            + abs(bh - expected_side_y) / max(1.0, float(expected_side_y))
        )
        proximity = 1.0 - min(1.0, dist / max(1.0, float(max(r_x, r_y))))
        score = proximity * 2.0 + solidity + fill_ratio + aspect - size_penalty
        if score > best_score:
            best_score = score
            best = (x1 + cx, y1 + cy)

    return best
