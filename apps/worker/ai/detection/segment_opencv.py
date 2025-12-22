# apps/worker/ai/detection/segment_opencv.py
from __future__ import annotations

from typing import List, Tuple
import cv2  # type: ignore

BBox = Tuple[int, int, int, int]


def segment_questions_opencv(image_path: str) -> List[BBox]:
    """
    legacy 섞여있던 opencv segmentation 정리본
    입력: image_path
    출력: [(x,y,w,h), ...]
    """
    image_bgr = cv2.imread(image_path)
    if image_bgr is None:
        return []

    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (5, 5), 0)

    _, thresh = cv2.threshold(
        blur, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU
    )

    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    dilated = cv2.dilate(thresh, kernel, iterations=1)

    contours, _ = cv2.findContours(
        dilated, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
    )

    h_img, w_img = gray.shape[:2]
    min_area = w_img * h_img * 0.005
    max_area = w_img * h_img * 0.9

    boxes: List[BBox] = []
    for cnt in contours:
        x, y, w, h = cv2.boundingRect(cnt)
        area = w * h
        if area < min_area or area > max_area:
            continue

        aspect = h / (w + 1e-6)
        if aspect < 0.3:
            continue

        boxes.append((x, y, w, h))

    boxes.sort(key=lambda b: (b[1], b[0]))
    return boxes
