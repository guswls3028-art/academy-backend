# PATH: academy/domain/tools/image_preprocessor.py
# Document-quality image preprocessing for PPT slides and question detection.
#
# Two modes:
#   - preprocess_for_export: high quality output for PPT slides
#   - preprocess_for_detect: aggressive processing for question boundary detection

from __future__ import annotations

from PIL import Image, ImageEnhance, ImageOps, ImageStat


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


def compact_internal_whitespace(img: Image.Image) -> Image.Image:
    """Shorten proven empty bands in tall question crops without removing ink.

    A portrait question with its choices near the bottom is otherwise shrunk to
    an unreadable width on a landscape slide. Only body-wide white bands
    between visible rows are shortened; thin page-edge rules may be shortened,
    while the question, figures and choices stay in their original order.
    """
    width, height = img.size
    if width <= 0 or height < width * 1.6:
        return img

    gray = img.convert("L")
    ink = gray.point(lambda value: 255 if value < 245 else 0)
    # Page-edge rules can span an otherwise empty band. Keep diagrams and text
    # in the body, but do not let a thin decorative rule force a tiny slide.
    inner_margin = max(1, int(width * 0.035))
    inner_ink = ink.crop((inner_margin, 0, width - inner_margin, height))
    # Find candidates cheaply, then verify every pixel before removing a band.
    row_density = list(ink.resize((1, height), Image.Resampling.BOX).getdata())
    min_gap = max(120, int(height * 0.12))
    keep_gap = max(48, min(120, int(width * 0.10)))
    gaps: list[tuple[int, int]] = []
    start = None
    for row, density in enumerate([*row_density, 255]):
        if density <= 2:
            if start is None:
                start = row
        elif start is not None:
            if (
                row - start > max(min_gap, keep_gap)
                and start > 0
                and row < height
                and inner_ink.crop((0, start, inner_ink.width, row)).getbbox() is None
            ):
                gaps.append((start, row))
            start = None

    if not gaps:
        return img

    pieces = []
    cursor = 0
    for start, end in gaps:
        pieces.append(img.crop((0, cursor, width, start + keep_gap)))
        cursor = end
    pieces.append(img.crop((0, cursor, width, height)))
    result = Image.new(img.mode, (width, sum(piece.height for piece in pieces)))
    top = 0
    for piece in pieces:
        result.paste(piece, (0, top))
        top += piece.height
    return result


def reflow_tall_question(img: Image.Image) -> Image.Image:
    """Place two intact reading sections side by side on a landscape slide.

    A split is made only inside a verified whitespace band. If the source has
    a continuous figure or no safe band, its original pixels remain in order.
    """
    width, height = img.size
    if width <= 0 or height <= width * 1.3:
        return img

    ink = img.convert("L").point(lambda value: 255 if value < 245 else 0)
    margin = max(1, int(width * 0.035))
    inner_ink = ink.crop((margin, 0, width - margin, height))
    density = list(ink.resize((1, height), Image.Resampling.BOX).getdata())
    gaps: list[tuple[int, int]] = []
    start = None
    for row, value in enumerate([*density, 255]):
        if value <= 2:
            if start is None:
                start = row
        elif start is not None:
            if (
                row - start >= max(20, int(height * 0.02))
                and height * 0.25 <= start
                and row <= height * 0.8
                and inner_ink.crop((0, start, inner_ink.width, row)).getbbox() is None
            ):
                gaps.append((start, row))
            start = None

    if not gaps:
        return img
    # The widest gap is more likely to be between a stem/figure and choices
    # than inside a table. Preserve every source row exactly once.
    gap_start, gap_end = max(gaps, key=lambda gap: (gap[1] - gap[0], -abs(sum(gap) - height)))
    split_at = (gap_start + gap_end) // 2
    left = img.crop((0, 0, width, split_at))
    right = img.crop((0, split_at, width, height))
    gutter = max(20, int(width * 0.04))
    result = Image.new(img.mode, (width * 2 + gutter, max(left.height, right.height)), "white")
    result.paste(left, (0, 0))
    result.paste(right, (width + gutter, 0))
    return result


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
