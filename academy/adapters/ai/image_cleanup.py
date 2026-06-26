"""Image cleanup helpers for public-facing matchup problem crops.

This adapter is intentionally conservative: it targets red/pink grading marks
only, leaving black handwriting and printed text untouched.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MarkCleanupResult:
    image_bytes: bytes
    mask_ratio: float
    mask_pixels: int
    total_pixels: int
    width: int
    height: int
    mode: str = "red_marks"
    version: str = "red-marks-v1"


def remove_colored_marks_from_image_bytes(image_bytes: bytes) -> MarkCleanupResult:
    """Remove red/pink grading marks from a PNG/JPEG image."""
    try:
        import cv2
        import numpy as np
    except Exception as exc:  # pragma: no cover - deployment dependency guard
        raise RuntimeError("OpenCV/Numpy dependencies are not available") from exc

    arr = np.frombuffer(image_bytes, dtype=np.uint8)
    image = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("image bytes could not be decoded")

    height, width = image.shape[:2]
    total_pixels = int(height * width)
    if total_pixels <= 0:
        raise ValueError("decoded image is empty")

    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    mask_red_low = cv2.inRange(hsv, (0, 18, 80), (16, 255, 255))
    mask_red_high = cv2.inRange(hsv, (154, 16, 80), (179, 255, 255))

    b, g, r = cv2.split(image)
    rgb_bias = (
        (r.astype("int16") > g.astype("int16") + 18)
        & (r.astype("int16") > b.astype("int16") + 18)
        & (r > 90)
    )
    pale_pink = (
        (r > 150)
        & (g > 115)
        & (b > 115)
        & (r.astype("int16") > g.astype("int16") + 5)
        & (r.astype("int16") > b.astype("int16") + 5)
        & (r.astype("int16") < g.astype("int16") + 65)
        & (r.astype("int16") < b.astype("int16") + 65)
        & (np.abs(g.astype("int16") - b.astype("int16")) < 34)
    )
    mask = cv2.bitwise_or(mask_red_low, mask_red_high)
    mask = cv2.bitwise_or(mask, (rgb_bias.astype("uint8") * 255))
    mask = cv2.bitwise_or(mask, (pale_pink.astype("uint8") * 255))

    kernel_open = np.ones((2, 2), np.uint8)
    kernel_dilate = np.ones((4, 4), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel_open)
    mask = cv2.dilate(mask, kernel_dilate, iterations=1)

    mask_pixels = int(cv2.countNonZero(mask))
    if mask_pixels == 0:
        cleaned = image
    else:
        cleaned = cv2.inpaint(image, mask, 3, cv2.INPAINT_TELEA)
        cleaned[mask > 0] = cv2.addWeighted(
            cleaned[mask > 0],
            0.8,
            np.full_like(cleaned[mask > 0], 255),
            0.2,
            0,
        )

    ok, encoded = cv2.imencode(".png", cleaned)
    if not ok:
        raise RuntimeError("cleaned image could not be encoded")

    return MarkCleanupResult(
        image_bytes=encoded.tobytes(),
        mask_ratio=mask_pixels / total_pixels,
        mask_pixels=mask_pixels,
        total_pixels=total_pixels,
        width=int(width),
        height=int(height),
    )
