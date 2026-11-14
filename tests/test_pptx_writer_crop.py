from __future__ import annotations

import io

import pytest
from PIL import Image
from pptx import Presentation

from academy.adapters.tools.pptx_writer import (
    SLIDE_DIMENSIONS,
    add_slide,
    create_presentation,
    save_to_bytes,
)


def _image_bytes(width: int, height: int) -> bytes:
    image = Image.new("RGB", (width, height), "white")
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


@pytest.mark.parametrize(
    ("image_size", "cropped_axis"),
    [
        ((2000, 1000), "horizontal"),
        ((1000, 2000), "vertical"),
    ],
)
def test_cover_fit_uses_real_center_crop_inside_slide(image_size, cropped_axis):
    slide_width, slide_height = SLIDE_DIMENSIONS["16:9"]
    presentation = create_presentation(slide_width, slide_height)

    add_slide(
        presentation,
        _image_bytes(*image_size),
        background_color="white",
        fit_mode="cover",
    )

    presentation = Presentation(io.BytesIO(save_to_bytes(presentation)))
    picture = next(shape for shape in presentation.slides[0].shapes if hasattr(shape, "image"))
    assert (picture.left, picture.top, picture.width, picture.height) == (
        0,
        0,
        slide_width,
        slide_height,
    )
    if cropped_axis == "horizontal":
        assert picture.crop_left == pytest.approx(picture.crop_right)
        assert picture.crop_left > 0
        assert picture.crop_top == 0
        assert picture.crop_bottom == 0
    else:
        assert picture.crop_top == pytest.approx(picture.crop_bottom)
        assert picture.crop_top > 0
        assert picture.crop_left == 0
        assert picture.crop_right == 0


@pytest.mark.parametrize("orientation", [2, 6, 8])
def test_image_export_applies_camera_orientation_without_effects(orientation):
    from PIL import ImageDraw, ImageOps

    from academy.application.use_cases.tools.generate_ppt import GeneratePptUseCase

    image = Image.new("RGB", (180, 100), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 40, 30), fill="red")
    draw.rectangle((110, 65, 179, 99), fill="blue")
    exif = Image.Exif()
    exif[274] = orientation
    with io.BytesIO() as buffer:
        image.save(buffer, format="JPEG", quality=95, exif=exif)
        data = buffer.getvalue()

    result = GeneratePptUseCase().execute([data])
    slide = Presentation(io.BytesIO(result.pptx_bytes)).slides[0]
    picture = slide.shapes[0]
    with Image.open(io.BytesIO(data)) as source, Image.open(io.BytesIO(picture.image.blob)) as exported:
        expected = ImageOps.exif_transpose(source)
        assert exported.size == expected.size
        assert exported.tobytes() == expected.tobytes()
        assert exported.getexif().get(274) is None
        assert picture.width / picture.height == pytest.approx(expected.width / expected.height, rel=1e-5)


def test_image_export_preserves_webp_pixels_and_transparency_without_effects():
    from academy.application.use_cases.tools.generate_ppt import GeneratePptUseCase

    image = Image.new("RGBA", (100, 180), (0, 0, 0, 0))
    image.paste((5, 50, 150, 200), (20, 30, 90, 110))
    with io.BytesIO() as buffer:
        image.save(buffer, format="WEBP", lossless=True)
        data = buffer.getvalue()

    result = GeneratePptUseCase().execute([data])
    picture = Presentation(io.BytesIO(result.pptx_bytes)).slides[0].shapes[0]
    with Image.open(io.BytesIO(picture.image.blob)) as exported:
        assert exported.format == "PNG"
        assert exported.mode == "RGBA"
        assert exported.tobytes() == image.tobytes()
