"""Printed empty choices must not become ink when correction fluid removes print."""
import cv2
import numpy as np
import pytest

from academy.adapters.ai.omr.engine import detect_omr_answers_v7


def printed_row(*, marks, erased=(), scale=1, paper=255, outline=110, pattern="filled", stroke=5):
    width, height = 800 * scale, 240 * scale
    image = np.full((height, width, 3), paper, dtype=np.uint8)
    choices = []
    for label, x in enumerate((100, 250, 400, 550, 700), 1):
        center = (x * scale, 120 * scale)
        radii = (15 * scale, 24 * scale)
        choices.append({"label": str(label), "center": {"x": x, "y": 120},
                        "radius_x": 15, "radius_y": 24})
        cv2.ellipse(image, center, radii, 0, 0, 360, (outline,) * 3,
                    max(1, scale), lineType=cv2.LINE_AA)
        cv2.putText(image, str(label), (center[0] - 6 * scale, center[1] + 7 * scale),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6 * scale, (outline,) * 3,
                    max(1, scale), lineType=cv2.LINE_AA)
        if label in erased:
            cv2.rectangle(image, (center[0] - radii[0] - 3 * scale, center[1] - radii[1] - 3 * scale),
                          (center[0] + radii[0] + 3 * scale, center[1] + radii[1] + 3 * scale),
                          (255,) * 3, -1)
        if label in marks:
            color = (marks[label],) * 3
            if pattern == "filled":
                cv2.ellipse(image, center, radii, 0, 0, 360, color, -1,
                            lineType=cv2.LINE_AA)
            elif pattern in {"diagonal", "cross"}:
                cv2.line(image, (center[0] - 10 * scale, center[1] + 17 * scale),
                         (center[0] + 10 * scale, center[1] - 17 * scale),
                         color, stroke * scale, lineType=cv2.LINE_AA)
                if pattern == "cross":
                    cv2.line(image, (center[0] - 10 * scale, center[1] - 17 * scale),
                             (center[0] + 10 * scale, center[1] + 17 * scale),
                             color, stroke * scale, lineType=cv2.LINE_AA)
            elif pattern == "hatching":
                for dy in (-12, -4, 4, 12):
                    cv2.line(image, (center[0] - 9 * scale, center[1] + dy * scale),
                             (center[0] + 9 * scale, center[1] + (dy - 3) * scale),
                             color, 2 * scale, lineType=cv2.LINE_AA)
            elif pattern == "half":
                cv2.ellipse(image, center, radii, 0, 90, 270, color, -1,
                            lineType=cv2.LINE_AA)
    meta = {"version": "v15", "page": {"width": 800, "height": 240},
            "questions": [{"question_number": 1, "choices": choices}]}
    return detect_omr_answers_v7(image_bgr=image, meta=meta)[0]


@pytest.mark.parametrize("scale", [1, 2, 3])
@pytest.mark.parametrize("erased", [(), (2,), (2, 4), (2, 3, 4)])
def test_whiteout_does_not_turn_printed_choices_into_marks(scale, erased):
    result = printed_row(marks={1: 65}, erased=erased, scale=scale)
    assert result.detected == ["1"], result.to_dict()
    assert result.marking == "single"
    assert result.status == "ok"


@pytest.mark.parametrize("paper,outline,ink", [(255, 110, 220), (235, 85, 195), (255, 160, 230)])
def test_faint_marks_survive_whiteout(paper, outline, ink):
    result = printed_row(marks={1: ink}, erased=(2, 4), paper=paper, outline=outline)
    assert result.detected == ["1"], result.to_dict()
    assert result.status == "ok"


@pytest.mark.parametrize("marks,erased", [({1: 65, 3: 195}, (2, 4)),
                                          ({1: 65, 3: 150, 5: 195}, (2, 4)),
                                          ({1: 65, 2: 100, 3: 120, 4: 150}, ()),
                                          ({1: 65, 2: 100, 3: 120, 4: 150, 5: 170}, ())])
def test_real_multiple_marks_are_not_reduced_to_the_darkest(marks, erased):
    result = printed_row(marks=marks, erased=erased)
    assert result.detected == [str(label) for label in marks], result.to_dict()
    assert result.marking == "multi"


@pytest.mark.parametrize("erased", [(), (2, 4), (1, 2, 3, 4, 5)])
def test_whiteout_and_print_alone_are_blank(erased):
    result = printed_row(marks={}, erased=erased)
    assert result.detected == [], result.to_dict()
    assert result.marking == "blank"


@pytest.mark.parametrize("pattern", ["diagonal", "cross", "hatching", "half"])
@pytest.mark.parametrize("marks", [{1: 65}, {1: 65, 3: 100}])
@pytest.mark.parametrize("erased", [(), (2, 4)])
@pytest.mark.parametrize("scale,stroke", [(1, 5), (2, 5), (3, 5), (1, 2), (2, 2), (3, 2)])
def test_partial_marks_are_preserved(pattern, marks, erased, scale, stroke):
    result = printed_row(marks=marks, erased=erased, pattern=pattern, scale=scale, stroke=stroke)
    assert result.detected == [str(label) for label in marks], result.to_dict()


@pytest.mark.parametrize("dpi", [150, 300])
@pytest.mark.parametrize("pattern", ["filled", "diagonal"])
def test_real_pdf_print_survives_whiteout_with_single_multiple_and_blank_rows(dpi, pattern):
    from apps.domains.assets.omr.services.meta_generator import build_omr_meta
    from tests.omr.test_omr_realuse import render_marked_pdf

    meta = build_omr_meta(question_count=20, n_choices=5)
    image = render_marked_pdf(meta, {}, {}, dpi=dpi)
    sx = image.shape[1] / meta["page"]["width"]
    sy = image.shape[0] / meta["page"]["height"]
    expected = {1: ["1"], 2: [], 3: ["1", "3"]}
    for question in meta["questions"][:3]:
        for choice in question["choices"]:
            x = round(choice["center"]["x"] * sx)
            y = round(choice["center"]["y"] * sy)
            rx = round(choice["radius_x"] * sx)
            ry = round(choice["radius_y"] * sy)
            if choice["label"] in {"2", "4"}:
                cv2.rectangle(image, (x - rx - 2, y - ry - 2),
                              (x + rx + 2, y + ry + 2), (255,) * 3, -1)
            elif choice["label"] in expected[question["question_number"]]:
                if pattern == "filled":
                    cv2.ellipse(image, (x, y), (rx, ry), 0, 0, 360, (65,) * 3, -1)
                else:
                    cv2.line(image, (x - round(rx * .6), y + round(ry * .7)),
                             (x + round(rx * .6), y - round(ry * .7)), (65,) * 3,
                             max(2, round(rx * .2)), lineType=cv2.LINE_AA)
    answers = detect_omr_answers_v7(image_bgr=image, meta=meta)
    for answer in answers:
        assert answer.detected == expected.get(answer.question_id, []), answer.to_dict()
