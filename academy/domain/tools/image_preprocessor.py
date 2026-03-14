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
