"""Image cleanup helpers for public-facing matchup problem crops.

This adapter targets red/pink grading marks and thick dark handwritten marks.
It intentionally avoids thin dark strokes, which usually belong to printed
Korean text, diagrams, tables, and answer choices.
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
    red_mask_pixels: int = 0
    dark_mask_pixels: int = 0
    red_mask_ratio: float = 0.0
    dark_mask_ratio: float = 0.0
    mode: str = "student_marks"
    version: str = "student-marks-v2"


def remove_colored_marks_from_image_bytes(image_bytes: bytes) -> MarkCleanupResult:
    """Remove grading marks and thick handwriting from a PNG/JPEG image."""
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

    red_mask = _build_red_mark_mask(image, cv2, np)
    dark_mask = _build_dark_handwriting_mask(image, cv2, np)
    mask = cv2.bitwise_or(red_mask, dark_mask)

    red_mask_pixels = int(cv2.countNonZero(red_mask))
    dark_mask_pixels = int(cv2.countNonZero(dark_mask))
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
        red_mask_pixels=red_mask_pixels,
        dark_mask_pixels=dark_mask_pixels,
        red_mask_ratio=red_mask_pixels / total_pixels,
        dark_mask_ratio=dark_mask_pixels / total_pixels,
    )


def _build_red_mark_mask(image, cv2, np):
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
    return cv2.dilate(mask, kernel_dilate, iterations=1)


def _build_dark_handwriting_mask(image, cv2, np):
    """Detect thick dark annotations while preserving thin printed strokes."""
    height, width = image.shape[:2]
    total_pixels = max(1, int(height * width))
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    dark = cv2.inRange(gray, 0, 76)

    # Thick marker strokes survive erosion; ordinary printed glyphs mostly vanish.
    seed = cv2.erode(dark, np.ones((3, 3), np.uint8), iterations=1)
    seed = cv2.dilate(seed, np.ones((3, 3), np.uint8), iterations=1)

    num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(seed, 8)
    keep = np.zeros_like(dark)
    min_area = max(80, int(total_pixels * 0.00004))
    max_area = int(total_pixels * 0.045)

    for idx in range(1, num_labels):
        x = int(stats[idx, cv2.CC_STAT_LEFT])
        y = int(stats[idx, cv2.CC_STAT_TOP])
        w = int(stats[idx, cv2.CC_STAT_WIDTH])
        h = int(stats[idx, cv2.CC_STAT_HEIGHT])
        area = int(stats[idx, cv2.CC_STAT_AREA])
        if area < min_area or area > max_area:
            continue
        if w < 8 or h < 8:
            continue
        aspect = max(w / max(h, 1), h / max(w, 1))
        if aspect > 14:
            continue
        density = area / max(w * h, 1)
        if density > 0.58:
            continue
        near_public_annotation_zone = (
            x < width * 0.14
            or y < height * 0.10
            or x + w > width * 0.86
            or y + h > height * 0.86
        )
        if not near_public_annotation_zone:
            continue
        touches_edge = x <= 1 or y <= 1 or x + w >= width - 1 or y + h >= height - 1
        if touches_edge and area > max(120, min_area * 5):
            continue

        component = (labels == idx).astype("uint8") * 255
        component = cv2.dilate(component, np.ones((9, 9), np.uint8), iterations=1)
        stroke = cv2.bitwise_and(dark, component)
        stroke = cv2.dilate(stroke, np.ones((4, 4), np.uint8), iterations=1)
        keep = cv2.bitwise_or(keep, stroke)

    return keep
