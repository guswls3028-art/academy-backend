# apps/worker/ai/handwriting/detector.py
from __future__ import annotations

from typing import Dict
import cv2  # type: ignore
import numpy as np  # type: ignore


def analyze_handwriting(image_path: str) -> Dict[str, float]:
    """
    legacy(doc_ai/handwriting/handwriting_detector.py) 이식본
    - 한 이미지에서 필기 흔적/계산식 형태 흔적 여부 점수 반환

    return:
      {
        "writing_score": 0~1,
        "calculation_score": 0~1
      }
    """
    img = cv2.imread(image_path, cv2.IMREAD_GRAYSCALE)
    if img is None:
        return {"writing_score": 0.0, "calculation_score": 0.0}

    blur = cv2.GaussianBlur(img, (5, 5), 0)
    edges = cv2.Canny(blur, 50, 150)

    writing_density = float(np.sum(edges > 0) / edges.size)

    sobel_x = cv2.Sobel(blur, cv2.CV_64F, 1, 0, ksize=5)
    sobel_y = cv2.Sobel(blur, cv2.CV_64F, 0, 1, ksize=5)
    grad_mag = (np.mean(np.abs(sobel_x)) + np.mean(np.abs(sobel_y))) / 255.0

    writing_score = min(max(writing_density * 12.0, 0.0), 1.0)
    calculation_score = min(max(grad_mag * 3.0, 0.0), 1.0)

    return {
        "writing_score": float(writing_score),
        "calculation_score": float(calculation_score),
    }
