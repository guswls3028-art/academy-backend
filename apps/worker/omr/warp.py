# apps/worker/ai_worker/ai/omr/warp.py
from __future__ import annotations

from typing import Optional, Tuple

import cv2  # type: ignore
import numpy as np  # type: ignore


def _order_points(pts: np.ndarray) -> np.ndarray:
    # pts: (4,2)
    rect = np.zeros((4, 2), dtype=np.float32)
    s = pts.sum(axis=1)
    rect[0] = pts[np.argmin(s)]  # top-left
    rect[2] = pts[np.argmax(s)]  # bottom-right

    diff = np.diff(pts, axis=1)
    rect[1] = pts[np.argmin(diff)]  # top-right
    rect[3] = pts[np.argmax(diff)]  # bottom-left
    return rect


def warp_to_a4_landscape(
    *,
    image_bgr: np.ndarray,
    out_size_px: Tuple[int, int] = (3508, 2480),  # 300dpi A4 landscape (근사)
) -> Optional[np.ndarray]:
    """
    촬영/프레임 이미지에서 문서(답안지) 외곽을 찾아 A4 landscape로 워프.
    성공하면 "페이지 전체 = 이미지 전체"가 되므로 meta ROI를 그대로 적용 가능.

    실패하면 None 반환 -> caller가 fallback(yolo/opencv segmentation 등) 처리
    """
    if image_bgr is None or image_bgr.size == 0:
        return None

    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(blur, 50, 150)

    # 윤곽 강화
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    edges = cv2.dilate(edges, kernel, iterations=1)

    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None

    contours = sorted(contours, key=cv2.contourArea, reverse=True)

    page_cnt = None
    for cnt in contours[:8]:
        peri = cv2.arcLength(cnt, True)
        approx = cv2.approxPolyDP(cnt, 0.02 * peri, True)
        if len(approx) == 4:
            page_cnt = approx
            break

    if page_cnt is None:
        return None

    pts = page_cnt.reshape(4, 2).astype(np.float32)
    rect = _order_points(pts)

    out_w, out_h = out_size_px
    dst = np.array(
        [
            [0, 0],
            [out_w - 1, 0],
            [out_w - 1, out_h - 1],
            [0, out_h - 1],
        ],
        dtype=np.float32,
    )

    M = cv2.getPerspectiveTransform(rect, dst)
    warped = cv2.warpPerspective(image_bgr, M, (out_w, out_h))
    return warped
