"""Vision boundary for additive essay scores; no student or grading state."""
import cv2
import numpy as np
import pytest
from pdf2image import convert_from_bytes

from academy.adapters.ai.omr.engine import AnswerDetectConfig, detect_omr_answers_v7, prepare_omr_images
from academy.adapters.ai.omr.essay_scores import detect_additive_essay_score_marks
from academy.adapters.ai.omr.paper_essay_scores import detect_paper_essay_scores
from academy.adapters.ai.omr.warp import align_to_a4_landscape
from apps.domains.assets.omr.dto.omr_document import OMRDocument
from apps.domains.assets.omr.renderer.pdf_renderer import OMRPdfRenderer
from apps.domains.assets.omr.services.meta_generator import build_essay_score_grid_metadata, build_omr_meta


def _sheet(density=8):
    meta = {"page": {"width": 297, "height": 210}}
    page = np.full((210 * density, 297 * density, 3), 255, dtype=np.uint8)

    def point(x, y):
        return round(x * density), round(y * density)

    marks = [
        {"x": 216 + bit * 2.8, "y": y, "size": 1.5, "filled": row == ((0x932A >> bit) & 1)}
        for bit in range(16)
        for row, y in enumerate((4.6, 7.0))
    ]
    for mark in marks:
        if not mark["filled"]:
            continue
        x, y, radius = mark["x"], mark["y"], mark["size"] / 2
        cv2.rectangle(page, point(x-radius, y-radius), point(x+radius, y+radius), (0, 0, 0), -1)
    meta["essay_score_layout_marks"] = marks

    choices = []
    for digit in range(9):
        x, y = 28 + (digit % 3) * 4.4, 91 + (digit // 3) * 6
        choices.append({"label": str(digit), "center": {"x": x, "y": y}, "radius_x": 1.8, "radius_y": 2.6})
        cx, cy = point(x, y)
        cv2.ellipse(page, (cx, cy), point(1.8, 2.6), 0, 0, 360, (0, 0, 0), 1, cv2.LINE_AA)
        glyph_size = density / 24
        (width, height), _ = cv2.getTextSize(str(digit), cv2.FONT_HERSHEY_SIMPLEX, glyph_size, 1)
        cv2.putText(page, str(digit), (cx-width//2, cy+height//2), cv2.FONT_HERSHEY_SIMPLEX,
                    glyph_size, (153, 153, 153), 1, cv2.LINE_AA)
    meta["essay_score_questions"] = [{"question_number": 21, "choices": choices}]

    def fill(digit, intensity):
        choice = choices[digit]
        center = point(choice["center"]["x"], choice["center"]["y"])
        cv2.ellipse(page, center, point(1.35, 1.95), 0, 0, 360, (intensity,) * 3, -1, cv2.LINE_AA)

    return page, meta, fill


def _read(page, meta, **kwargs):
    prepared = prepare_omr_images(image_bgr=page, meta=meta, config=AnswerDetectConfig())
    return detect_additive_essay_score_marks(meta=meta, prepared_images=prepared, **kwargs)


@pytest.mark.parametrize(
    "fills, status, values, score",
    [
        ([], "blank", None, None),
        ([(0, 35)], "ok", [0], 0),
        ([(7, 30), (8, 135)], "ok", [7, 8], 15),
        ([(n, 65) for n in range(1, 9)], "ok", list(range(1, 9)), 36),
        ([(0, 65), (8, 35)], "ambiguous", None, None),
        ([(7, 30), (8, 220)], "ambiguous", None, None),
    ],
)
def test_additive_essay_score_states(fills, status, values, score):
    page, meta, fill = _sheet()
    for digit, intensity in fills:
        fill(digit, intensity)

    [result] = _read(page, meta)

    assert result["question_number"] == 21
    assert result["status"] == status
    assert result.get("marked_values") == values
    assert result["score"] == score


def test_layout_must_match_every_bit_and_require_alignment():
    page, meta, fill = _sheet()
    fill(8, 25)
    assert _read(page, meta, alignment_confirmed=False)[0]["status"] == "no_score_area"

    for mark in meta["essay_score_layout_marks"][:2]:
        mark["filled"] = not mark["filled"]
    assert _read(page, meta)[0]["status"] == "no_score_area"

    meta.pop("essay_score_layout_marks")
    assert _read(page, meta)[0]["status"] == "no_score_area"

    meta.pop("essay_score_questions")
    assert _read(page, meta) == []


@pytest.mark.parametrize("dpi", [150, 300])
def test_rendered_pdf_roundtrip_keeps_printed_digits_blank_and_reads_marks(dpi):
    choices, essays = list(range(1, 21)), [21, 22]
    objective_only = build_omr_meta(question_count=len(choices))
    meta = {
        **objective_only,
        **build_essay_score_grid_metadata(choice_numbers=choices, essay_numbers=essays),
    }
    doc = OMRDocument(
        exam_title="Essay score grid", mc_count=len(choices), essay_count=len(essays),
        essay_score_questions=tuple(meta["essay_score_questions"]),
        essay_score_layout_marks=tuple(meta["essay_score_layout_marks"]),
    )
    rendered, = convert_from_bytes(OMRPdfRenderer().render(doc), dpi=dpi)
    page = cv2.cvtColor(np.asarray(rendered), cv2.COLOR_RGB2BGR)

    def align(raster):
        aligned = align_to_a4_landscape(image_bgr=raster, meta=objective_only)
        assert aligned.success and aligned.method != "rotation_only"
        return aligned.image

    def objective_answers(aligned):
        return [
            (answer.question_id, answer.status, answer.detected)
            for answer in detect_omr_answers_v7(image_bgr=aligned, meta=objective_only)
        ]

    def fill(bubble, coverage):
        c = bubble["center"]
        cv2.ellipse(
            page,
            (round(c["x"] * sx), round(c["y"] * sy)),
            (round(bubble["radius_x"] * sx * coverage), round(bubble["radius_y"] * sy * coverage)),
            0, 0, 360, (70, 70, 70), -1, cv2.LINE_AA,
        )

    sx, sy = page.shape[1] / meta["page"]["width"], page.shape[0] / meta["page"]["height"]
    for question in objective_only["questions"][:2]:
        fill(question["choices"][2], 0.9)
    aligned_blank = align(page)
    assert [result["status"] for result in _read(aligned_blank, meta)] == ["blank", "blank"]
    expected = objective_answers(aligned_blank)
    assert len(expected) == 20
    assert [selected for _, _, selected in expected[:2]] == [["3"], ["3"]]

    for question, digits in zip(meta["essay_score_questions"], ((0,), (7, 8))):
        for digit in digits:
            fill(question["choices"][digit], 0.78)
    aligned_marked = align(page)
    assert [result.get("marked_values") for result in _read(aligned_marked, meta)] == [[0], [7, 8]]
    assert objective_answers(aligned_marked) == expected
    assert _read(aligned_marked, objective_only) == []
    wrong_exam_meta = {
        **objective_only,
        **build_essay_score_grid_metadata(choice_numbers=choices, essay_numbers=[21, 24]),
    }
    assert [result["status"] for result in _read(aligned_marked, wrong_exam_meta)] == ["no_score_area"] * 2


def test_unsigned_paper_requires_matching_print_geometry_and_preserves_zero_and_blank():
    objective = list(range(1, 19))
    essays = list(range(19, 23))
    meta = {
        **build_omr_meta(question_count=len(objective)),
        **build_essay_score_grid_metadata(choice_numbers=objective, essay_numbers=essays),
        "allow_unsigned_essay_score_grid": True,
    }
    doc = OMRDocument(exam_title="Paper form", mc_count=18, essay_count=4).with_paper_score_grid()
    rendered, = convert_from_bytes(OMRPdfRenderer().render(doc), dpi=180)
    page = cv2.cvtColor(np.asarray(rendered), cv2.COLOR_RGB2BGR)
    sx, sy = page.shape[1] / 297, page.shape[0] / 210
    for question, values in zip(meta["essay_score_questions"], [[0], [3], [], [7, 8]]):
        for value in values:
            center = question["choices"][value]["center"]
            cv2.ellipse(page, (round(center["x"]*sx), round(center["y"]*sy)),
                        (round(1.55*sx), round(2.15*sy)), 0, 0, 360, (30, 30, 30), -1)
    aligned = align_to_a4_landscape(image_bgr=page, meta=meta)
    assert aligned.success

    def read(image, metadata=meta, aligned_ok=True):
        prepared = prepare_omr_images(image_bgr=image, meta=metadata, config=AnswerDetectConfig())
        return detect_paper_essay_scores(meta=metadata, prepared_images=prepared, alignment_confirmed=aligned_ok)

    assert [(r["status"], r["score"]) for r in read(aligned.image)] == [
        ("ok", 0), ("ok", 3), ("blank", None), ("ok", 15),
    ]
    assert read(aligned.image, aligned_ok=False) == []
    assert read(aligned.image, {**meta, "allow_unsigned_essay_score_grid": False}) == []
    wrong_rows = {**meta, **build_omr_meta(question_count=17)}
    assert read(aligned.image, wrong_rows) == []

    signed = aligned.image.copy()
    sx, sy = signed.shape[1] / 297, signed.shape[0] / 210
    for mark in meta["essay_score_layout_marks"]:
        if not mark["filled"]:  # A different physical signature.
            x, y = mark["x"], mark["y"]
            cv2.rectangle(signed, (round((x-.75)*sx), round((y-.75)*sy)),
                          (round((x+.75)*sx), round((y+.75)*sy)), (0, 0, 0), -1)
    assert read(signed) == []
