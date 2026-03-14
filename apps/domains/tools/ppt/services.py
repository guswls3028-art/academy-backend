# PATH: apps/domains/tools/ppt/services.py
# PPT 생성 서비스 — python-pptx + Pillow
#
# 기능:
# - 이미지 → 슬라이드 (16:9, 4:3 지원)
# - 흑백 반전 (빔프로젝터용)
# - 배경색 설정 (검정/흰색/커스텀)
# - 이미지 비율 유지하면서 슬라이드에 최대 크기 배치

from __future__ import annotations

import io
import logging
from typing import Literal

from PIL import Image, ImageOps
from pptx import Presentation
from pptx.util import Inches, Emu
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE_TYPE

logger = logging.getLogger(__name__)

# Slide dimensions in EMU (English Metric Units, 1 inch = 914400 EMU)
SLIDE_DIMENSIONS = {
    "16:9": (Inches(13.333), Inches(7.5)),
    "4:3": (Inches(10), Inches(7.5)),
}

BACKGROUND_COLORS = {
    "black": RGBColor(0, 0, 0),
    "white": RGBColor(255, 255, 255),
    "dark_gray": RGBColor(30, 30, 30),
}


def _process_image(
    image_bytes: bytes,
    *,
    invert: bool = False,
    grayscale: bool = False,
) -> bytes:
    """이미지 전처리: 반전, 그레이스케일 등."""
    img = Image.open(io.BytesIO(image_bytes))

    # EXIF 회전 보정
    img = ImageOps.exif_transpose(img)

    # RGBA → RGB 변환 (PPT 호환)
    if img.mode == "RGBA":
        background = Image.new("RGB", img.size, (255, 255, 255))
        background.paste(img, mask=img.split()[3])
        img = background
    elif img.mode != "RGB":
        img = img.convert("RGB")

    if grayscale:
        img = ImageOps.grayscale(img)
        img = img.convert("RGB")

    if invert:
        img = ImageOps.invert(img)

    out = io.BytesIO()
    img.save(out, format="PNG", optimize=True)
    out.seek(0)
    return out.read()


def _fit_image_to_slide(
    img_width: int,
    img_height: int,
    slide_width: int,
    slide_height: int,
    fit_mode: str = "contain",
) -> tuple[int, int, int, int]:
    """이미지를 슬라이드에 맞추는 위치/크기 계산 (EMU 단위).

    fit_mode:
      - "contain": 비율 유지, 슬라이드 안에 최대 크기 (기본)
      - "cover": 비율 유지, 슬라이드 전체 덮기 (일부 잘림)
      - "stretch": 비율 무시, 슬라이드 꽉 채움

    Returns: (left, top, width, height) in EMU.
    """
    if fit_mode == "stretch":
        return 0, 0, slide_width, slide_height

    img_ratio = img_width / img_height
    slide_ratio = slide_width / slide_height

    if fit_mode == "cover":
        if img_ratio > slide_ratio:
            height = slide_height
            width = int(height * img_ratio)
        else:
            width = slide_width
            height = int(width / img_ratio)
    else:  # contain
        if img_ratio > slide_ratio:
            width = slide_width
            height = int(width / img_ratio)
        else:
            height = slide_height
            width = int(height * img_ratio)

    left = (slide_width - width) // 2
    top = (slide_height - height) // 2
    return left, top, width, height


def generate_ppt(
    images: list[tuple[str, bytes]],
    *,
    aspect_ratio: Literal["16:9", "4:3"] = "16:9",
    background: str = "black",
    fit_mode: str = "contain",
    invert: bool = False,
    grayscale: bool = False,
    per_slide_settings: list[dict] | None = None,
) -> bytes:
    """PPT 생성.

    Args:
        images: (파일명, 이미지바이트) 리스트. 순서대로 슬라이드 생성.
        aspect_ratio: 슬라이드 비율 ("16:9" 또는 "4:3").
        background: 배경색 ("black", "white", "dark_gray" 또는 hex "#RRGGBB").
        fit_mode: 이미지 배치 모드 ("contain", "cover", "stretch").
        invert: True면 모든 이미지 흑백 반전.
        grayscale: True면 모든 이미지 그레이스케일 변환.
        per_slide_settings: 슬라이드별 개별 설정 (invert, grayscale 등).

    Returns:
        PPTX 파일 바이트.
    """
    prs = Presentation()

    # 슬라이드 크기 설정
    slide_w, slide_h = SLIDE_DIMENSIONS.get(aspect_ratio, SLIDE_DIMENSIONS["16:9"])
    prs.slide_width = slide_w
    prs.slide_height = slide_h

    # 배경색 결정
    bg_color = BACKGROUND_COLORS.get(background)
    if bg_color is None and background.startswith("#") and len(background) == 7:
        try:
            r = int(background[1:3], 16)
            g = int(background[3:5], 16)
            b = int(background[5:7], 16)
            bg_color = RGBColor(r, g, b)
        except ValueError:
            bg_color = BACKGROUND_COLORS["black"]
    elif bg_color is None:
        bg_color = BACKGROUND_COLORS["black"]

    # 빈 레이아웃 (blank slide layout — 일반적으로 index 6)
    blank_layout = prs.slide_layouts[6]

    for idx, (filename, raw_bytes) in enumerate(images):
        # 슬라이드별 개별 설정
        slide_invert = invert
        slide_grayscale = grayscale
        if per_slide_settings and idx < len(per_slide_settings):
            ss = per_slide_settings[idx]
            slide_invert = ss.get("invert", invert)
            slide_grayscale = ss.get("grayscale", grayscale)

        # 이미지 전처리
        try:
            processed = _process_image(
                raw_bytes,
                invert=slide_invert,
                grayscale=slide_grayscale,
            )
        except Exception:
            logger.warning("이미지 처리 실패: %s (idx=%d), 원본 사용", filename, idx)
            processed = raw_bytes

        # 이미지 크기 파악
        img = Image.open(io.BytesIO(processed))
        img_w, img_h = img.size

        # 슬라이드 추가
        slide = prs.slides.add_slide(blank_layout)

        # 배경색 설정
        bg = slide.background
        fill = bg.fill
        fill.solid()
        fill.fore_color.rgb = bg_color

        # 이미지 위치/크기 계산 (EMU)
        left, top, width, height = _fit_image_to_slide(
            img_w, img_h,
            prs.slide_width, prs.slide_height,
            fit_mode=fit_mode,
        )

        # 이미지 추가
        img_stream = io.BytesIO(processed)
        slide.shapes.add_picture(img_stream, left, top, width, height)

    # PPTX 바이트 출력
    output = io.BytesIO()
    prs.save(output)
    output.seek(0)
    return output.read()
