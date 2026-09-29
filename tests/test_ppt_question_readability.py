from PIL import Image, ImageDraw

from academy.domain.tools.image_preprocessor import compact_internal_whitespace


def test_tall_question_keeps_all_visible_content_on_one_readable_image():
    image = Image.new("RGB", (600, 1800), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((30, 40, 570, 210), fill="black")  # question and figure
    draw.rectangle((30, 1480, 570, 1660), fill="black")  # choices

    compacted = compact_internal_whitespace(image)

    assert compacted.width == image.width
    assert compacted.height < 900
    assert compacted.getpixel((100, 100)) == (0, 0, 0)
    assert compacted.getpixel((100, compacted.height - 200)) == (0, 0, 0)
    assert compacted.convert("L").histogram()[0] == image.convert("L").histogram()[0]


def test_diagram_line_prevents_blank_band_removal():
    image = Image.new("RGB", (600, 1800), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((30, 40, 570, 210), fill="black")
    draw.line((300, 210, 300, 1480), fill="black", width=1)
    draw.rectangle((30, 1480, 570, 1660), fill="black")

    assert compact_internal_whitespace(image).height == image.height
