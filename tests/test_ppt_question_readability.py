from PIL import Image, ImageDraw

from academy.domain.tools.image_preprocessor import compact_internal_whitespace, reflow_tall_question


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


def test_decorative_page_edge_rule_does_not_keep_large_empty_band():
    image = Image.new("RGB", (600, 1800), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((30, 40, 570, 210), fill="black")
    draw.line((5, 210, 5, 1480), fill="black", width=1)
    draw.rectangle((30, 1480, 570, 1660), fill="black")

    compacted = compact_internal_whitespace(image)

    assert compacted.height < 900
    assert compacted.getpixel((100, 100)) == (0, 0, 0)
    assert compacted.getpixel((100, compacted.height - 200)) == (0, 0, 0)


def test_tall_question_reflows_at_blank_section_without_losing_rows():
    image = Image.new("RGB", (600, 1400), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((20, 20, 580, 530), outline="black", width=3)
    draw.rectangle((20, 870, 580, 1380), outline="black", width=3)

    reflowed = reflow_tall_question(image)

    assert reflowed.width == 1224
    assert reflowed.height < image.height
    split_at = reflowed.height
    assert reflowed.crop((0, 0, 600, split_at)).tobytes() == image.crop((0, 0, 600, split_at)).tobytes()
    assert reflowed.crop((624, 0, 1224, image.height - split_at)).tobytes() == image.crop((0, split_at, 600, image.height)).tobytes()


def test_tall_question_with_continuous_figure_is_not_reflowed():
    image = Image.new("RGB", (600, 1400), "white")
    draw = ImageDraw.Draw(image)
    draw.line((300, 0, 300, 1400), fill="black", width=2)

    assert reflow_tall_question(image) is image
