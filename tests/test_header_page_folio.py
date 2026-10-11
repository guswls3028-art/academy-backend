import pytest

@pytest.mark.parametrize("embedded", [False, True])
@pytest.mark.parametrize("folio_x", [25.0, 525.0])
def test_corner_header_folio_does_not_create_question_or_split_wide_body(embedded, folio_x):
    from academy.domain.tools.paper_type import classify_paper_type
    from academy.domain.tools.question_splitter import TextBlock as TB, split_questions

    blocks = [
        TB(text="7 / 7", x0=folio_x, y0=50, x1=folio_x + 35, y1=62),
        TB(text="27.\n다항식 P(x)를 정리하여 P(2)의 값을 구하시오.", x0=18, y0=147, x1=271, y1=159),
        TB(text="P(x) = (x + 1)(x + 2) + (x - 1)(x - 2)", x0=32, y0=192, x1=410, y1=206),
        TB(text="28.\n수직선에 나타낸 구간을 부등식으로 쓰시오.", x0=18, y0=340, x1=263, y1=352),
    ]
    paper = classify_paper_type(
        text_blocks=blocks, page_width=600, page_height=840, has_embedded_text=embedded,
    )
    regions = split_questions(blocks, 600, 840, page_index=6, paper_type=paper)

    assert [r.number for r in regions] == [27, 28]
    assert regions[0].bbox[2] >= 410


@pytest.mark.parametrize(
    "text,x0,y0,x1,y1",
    [
        ("7 / 구하시오.", 525, 50, 590, 65),  # OCR slash question marker.
        ("7 / 7", 250, 50, 290, 65),  # Centered mathematical fraction.
        ("7 / 7", 525, 300, 560, 315),  # Body fraction.
        ("7 / 7\n다음 식의 값을 구하시오.", 525, 50, 590, 80),
    ],
)
def test_header_folio_filter_preserves_question_text_and_body_fractions(text, x0, y0, x1, y1):
    from academy.domain.tools.question_splitter import TextBlock as TB, _looks_like_header_folio

    assert not _looks_like_header_folio(
        TB(text=text, x0=x0, y0=y0, x1=x1, y1=y1), page_width=600, page_height=840,
    )

