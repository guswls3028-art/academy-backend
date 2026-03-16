# PATH: academy/domain/tools/image_preprocessor.py
# Document-quality image preprocessing for PPT slides and question detection.
#
# Two modes:
#   - preprocess_for_export: high quality output for PPT slides
#   - preprocess_for_detect: aggressive processing for question boundary detection

from __future__ import annotations

from PIL import Image, ImageEnhance, ImageFilter, ImageOps, ImageStat


def preprocess_for_export(img: Image.Image) -> Image.Image:
    """High quality preprocessing for PPT slide export.

    Goals:
    - Readable black-and-white text
    - No brightness increase (no watermark amplification)
    - Histogram-based contrast normalization
    - Sharpness enhancement for text edges

    Args:
        img: PIL Image (any mode).

    Returns:
        Preprocessed PIL Image in RGB mode.
    """
    if img.mode == "RGBA":
        background = Image.new("RGB", img.size, (255, 255, 255))
        background.paste(img, mask=img.split()[3])
        img = background
    elif img.mode != "RGB":
        img = img.convert("RGB")

    # Analyze contrast via grayscale statistics
    gray = img.convert("L")
    stat = ImageStat.Stat(gray)
    stddev = stat.stddev[0] if stat.stddev else 0

    # Autocontrast with conservative cutoff — normalizes histogram
    # without amplifying watermarks or faint background elements
    img = ImageOps.autocontrast(img, cutoff=0.5)

    # Stddev-aware contrast enhancement (only for low-contrast images)
    if stddev < 40:
        img = ImageEnhance.Contrast(img).enhance(1.4)
    elif stddev < 60:
        img = ImageEnhance.Contrast(img).enhance(1.2)

    # Sharpness enhancement for text edges
    img = ImageEnhance.Sharpness(img).enhance(1.5)

    return img


def trim_bottom_whitespace(img: Image.Image, padding_px: int = 12) -> Image.Image:
    """Crop된 문항 이미지의 하단 여백을 제거하되, 내용 손실 방지.

    마지막 non-white 행을 찾아 그 아래를 잘라냄.
    padding_px만큼 여유를 남김 (내용 손실 방지).

    Args:
        img: PIL Image (RGB or L).
        padding_px: 하단에 남길 여백 (px).

    Returns:
        하단 여백이 제거된 PIL Image.
    """
    gray = img.convert("L") if img.mode != "L" else img
    width, height = gray.size

    # 상단은 그대로, 하단에서 위로 스캔하며 non-white 행 찾기
    # threshold 240: 거의 흰색이 아닌 행 = content 있음
    threshold = 240
    last_content_row = height - 1

    for y in range(height - 1, -1, -1):
        row = gray.crop((0, y, width, y + 1))
        pixels = list(row.getdata())
        # 행의 5% 이상이 threshold 이하면 content 행
        dark_count = sum(1 for p in pixels if p < threshold)
        if dark_count > width * 0.02:
            last_content_row = y
            break

    # 내용 행 + padding
    new_bottom = min(height, last_content_row + padding_px)

    # 최소 높이 보장 (너무 작아지면 원본 유지)
    if new_bottom < height * 0.3:
        return img

    if new_bottom < height - 5:  # 5px 이상 절약되면 trim
        return img.crop((0, 0, width, new_bottom))
    return img


def preprocess_for_detect(img: Image.Image) -> Image.Image:
    """Aggressive preprocessing for question boundary detection.

    Produces a clean binary image suitable for contour/region detection:
    - Grayscale conversion
    - Strong contrast boost
    - Threshold to binary

    Args:
        img: PIL Image (any mode).

    Returns:
        Preprocessed PIL Image in L (grayscale) mode, thresholded.
    """
    if img.mode != "L":
        img = img.convert("L")

    # Strong contrast
    img = ImageEnhance.Contrast(img).enhance(2.0)

    # Autocontrast with aggressive cutoff
    img = ImageOps.autocontrast(img, cutoff=2.0)

    # Threshold to binary (text = black, background = white)
    img = img.point(lambda x: 255 if x > 160 else 0, mode="L")

    return img
