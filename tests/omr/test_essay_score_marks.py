"""Teacher score strips: layout, real-PDF reading and fail-closed cases.

Synthetic regression over the printed PDF; not an accuracy figure for real scans.
"""
import cv2
import numpy as np
import pytest
from pdf2image import convert_from_bytes

from academy.adapters.ai.omr.engine import (
    AnswerDetectConfig,
    detect_omr_answers_v7,
    prepare_omr_images,
)
from academy.adapters.ai.omr.essay_scores import detect_essay_score_marks
from academy.adapters.ai.omr.warp import align_to_a4_landscape
from apps.domains.assets.omr.dto.omr_document import OMRDocument
from apps.domains.assets.omr.renderer.pdf_renderer import OMRPdfRenderer
from apps.domains.assets.omr.services.meta_generator import (
    build_essay_score_marks,
    build_omr_meta,
    essay_score_mark_availability,
)


def _meta(mc=30, essays=2, *, marks=True):
    numbers = list(range(mc + 1, mc + essays + 1))
    return build_omr_meta(
        question_count=mc,
        essay_score_mark_numbers=numbers if marks else None,
    ), numbers


def _render(meta, *, mc, essays, with_strip=True, dpi=200):
    doc = OMRDocument(
        exam_title="채점란",
        mc_count=mc,
        essay_count=essays,
        essay_score_marks=tuple(meta.get("essay_score_marks", ())) if with_strip else (),
    )
    page = convert_from_bytes(OMRPdfRenderer().render(doc), dpi=dpi)[0]
    return cv2.cvtColor(np.array(page), cv2.COLOR_RGB2BGR)


def _fill(image, meta, bubble, intensity=40, pattern="filled"):
    sx = image.shape[1] / meta["page"]["width"]
    sy = image.shape[0] / meta["page"]["height"]
    x = round(bubble["center"]["x"] * sx)
    y = round(bubble["center"]["y"] * sy)
    rx = round(bubble["radius_x"] * sx)
    ry = round(bubble["radius_y"] * sy)
    if pattern == "filled":
        cv2.ellipse(image, (x, y), (rx, ry), 0, 0, 360, (intensity,) * 3, -1)
    else:
        cv2.line(image, (x - round(rx * .6), y + round(ry * .7)),
                 (x + round(rx * .6), y - round(ry * .7)), (intensity,) * 3,
                 max(2, round(rx * .25)), lineType=cv2.LINE_AA)


def _mark_score(image, meta, mark, *, tens=None, ones=None, tenths=None, **kwargs):
    groups = {group["place"]: group for group in mark["groups"]}
    for place, value in (("tens", tens), ("ones", ones), ("tenths", tenths)):
        if value is not None:
            _fill(image, meta, groups[place]["bubbles"][value], **kwargs)


@pytest.mark.parametrize(("mc", "max_essays", "lines"), [(0, 10, 1), (20, 10, 1), (30, 8, 2), (40, 8, 2)])
def test_layout_is_fixed_and_fits_every_supported_essay_row(mc, max_essays, lines):
    for essays in range(1, max_essays + 2):
        availability = essay_score_mark_availability(
            question_count=mc, essay_count=essays, essay_max_scores=[10] * essays,
        )
        assert availability["available"] is (essays <= max_essays), (mc, essays, availability)
        if not availability["available"]:
            continue
        assert availability["lines"] == lines
        marks = build_essay_score_marks(
            question_count=mc,
            essay_question_numbers=list(range(mc + 1, mc + essays + 1)),
        )
        for mark in marks:
            assert mark["lines"] == lines
            assert mark["strip"]["x"] > mark["row"]["x"] + 8
            assert mark["strip"]["y"] + mark["strip"]["h"] <= mark["row"]["y"] + mark["row"]["h"]
            assert [len(g["bubbles"]) for g in mark["groups"]] == [5, 10, 10]
            centers = sorted(
                (b["center"]["y"], b["center"]["x"]) for g in mark["groups"] for b in g["bubbles"]
            )
            # No two bubbles closer than one slot on a line.
            for (y1, x1), (y2, x2) in zip(centers, centers[1:]):
                assert y1 != y2 or x2 - x1 >= 5.0 - 1e-6
    # Max score never moves the bubbles: printed sheets stay readable after edits.
    a = build_essay_score_marks(question_count=30, essay_question_numbers=[31, 32])
    b = build_essay_score_marks(question_count=30, essay_question_numbers=[31, 32])
    assert a == b


@pytest.mark.parametrize(
    ("essays", "maxes", "reason"),
    [
        (11, [10] * 11, "공간이 부족"),
        (2, [10, 60], "49.9점"),
        (2, [10, None], "배점이 없는"),
        (0, [], "서술형 문항이 없습니다"),
    ],
)
def test_unavailable_layouts_explain_the_reason(essays, maxes, reason):
    availability = essay_score_mark_availability(
        question_count=30, essay_count=essays, essay_max_scores=maxes,
    )
    assert availability["available"] is False
    assert reason in availability["reason"]


def test_meta_without_marks_is_unchanged():
    plain, _ = _meta(marks=False)
    assert "essay_score_marks" not in plain
    assert plain == build_omr_meta(question_count=30)


@pytest.mark.parametrize("dpi", [150, 300])
@pytest.mark.parametrize("pattern", ["filled", "diagonal"])
def test_teacher_marks_read_from_real_pdf(dpi, pattern):
    # Two-line strip (30 MC) and one-line strip (20 MC).
    for mc in (30, 20):
        meta, _ = _meta(mc, 4)
        image = _render(meta, mc=mc, essays=4, dpi=dpi)
        first, second, third, fourth = meta["essay_score_marks"]
        _mark_score(image, meta, first, ones=1, tenths=2, pattern=pattern)
        _mark_score(image, meta, second, ones=2, tenths=9, pattern=pattern)
        _mark_score(image, meta, third, tens=1, ones=0, pattern=pattern)
        aligned = align_to_a4_landscape(image_bgr=image, meta=meta).image
        reads = {
            r["question_number"]: r for r in detect_essay_score_marks(image_bgr=aligned, meta=meta)
        }
        expected = {mc + 1: 1.2, mc + 2: 2.9, mc + 3: 10.0}
        for number, score in expected.items():
            assert reads[number]["status"] == "ok", (mc, number, reads[number]["groups"])
            assert reads[number]["score"] == score
        assert reads[mc + 4]["status"] == "blank" and reads[mc + 4]["score"] is None


def test_zero_decimal_max_and_missing_ones_rules():
    meta, _ = _meta(20, 6)
    image = _render(meta, mc=20, essays=6)
    marks = meta["essay_score_marks"]
    _mark_score(image, meta, marks[0], ones=0)                     # 0점
    _mark_score(image, meta, marks[1], ones=0, tenths=7)           # 0.7점
    _mark_score(image, meta, marks[2], tens=4, ones=9, tenths=9)   # 49.9점
    _mark_score(image, meta, marks[3], tens=1)                     # 일의 자리 없음
    _mark_score(image, meta, marks[4], tenths=5)                   # 소수만
    _mark_score(image, meta, marks[5], ones=8)                     # 8점
    reads = detect_essay_score_marks(image_bgr=image, meta=meta)
    assert [(r["status"], r["score"]) for r in reads] == [
        ("ok", 0.0), ("ok", 0.7), ("ok", 49.9), ("ambiguous", None), ("ambiguous", None), ("ok", 8.0),
    ]


def test_double_marked_digit_is_ambiguous_not_guessed():
    meta, _ = _meta(30, 2)
    image = _render(meta, mc=30, essays=2)
    mark = meta["essay_score_marks"][0]
    _mark_score(image, meta, mark, ones=3)
    _mark_score(image, meta, mark, ones=8)
    read = detect_essay_score_marks(image_bgr=image, meta=meta)[0]
    assert read["status"] == "ambiguous"
    assert read["score"] is None
    assert sorted(read["groups"]["ones"]["marked"]) == ["3", "8"]


def test_sheet_printed_without_strip_is_not_read_even_with_handwriting():
    meta, _ = _meta(30, 2)
    image = _render(meta, mc=30, essays=2, with_strip=False)
    sx = image.shape[1] / meta["page"]["width"]
    sy = image.shape[0] / meta["page"]["height"]
    for mark in meta["essay_score_marks"]:
        strip = mark["strip"]
        # Student handwriting crossing the strip area.
        y = round((strip["y"] + strip["h"] / 2) * sy)
        for k in range(6):
            x0 = round((strip["x"] + 10 + k * 15) * sx)
            cv2.putText(image, "x=7", (x0, y), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (30,) * 3, 4)
    reads = detect_essay_score_marks(image_bgr=image, meta=meta)
    assert [r["status"] for r in reads] == ["no_score_area", "no_score_area"]


def test_objective_answers_are_unaffected_by_strip():
    meta, _ = _meta(30, 2)
    image = _render(meta, mc=30, essays=2)
    _mark_score(image, meta, meta["essay_score_marks"][0], tens=0, ones=9)
    for question in meta["questions"][:3]:
        _fill(image, meta, question["choices"][2])
    answers = detect_omr_answers_v7(image_bgr=image, meta=meta)
    assert [a.detected for a in answers[:3]] == [["3"], ["3"], ["3"]]
    assert all(a.status == "blank" for a in answers[3:])


def test_meta_without_strip_returns_no_reads():
    plain, _ = _meta(marks=False)
    image = np.full((1240, 1754, 3), 255, dtype=np.uint8)
    assert detect_essay_score_marks(image_bgr=image, meta=plain) == []


def test_shared_preprocessing_preserves_objective_and_essay_reads():
    meta, _ = _meta(mc=30, essays=2)
    image = _render(meta, mc=30, essays=2, dpi=300)
    _mark_score(image, meta, meta["essay_score_marks"][0], ones=3, tenths=5)
    config = AnswerDetectConfig()
    expected_answers = detect_omr_answers_v7(image_bgr=image, meta=meta, config=config)
    expected_essays = detect_essay_score_marks(image_bgr=image, meta=meta, config=config)
    prepared = prepare_omr_images(image_bgr=image, meta=meta, config=config)

    answers = detect_omr_answers_v7(
        image_bgr=image, meta=meta, config=config, prepared_images=prepared,
    )
    essays = detect_essay_score_marks(
        image_bgr=image, meta=meta, config=config, prepared_images=prepared,
    )

    assert [answer.to_dict() for answer in answers] == [answer.to_dict() for answer in expected_answers]
    assert essays == expected_essays
