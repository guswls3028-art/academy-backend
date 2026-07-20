from __future__ import annotations

import cv2
import pytest

from academy.adapters.ai.omr.engine import AnswerDetectConfig, detect_omr_answers_v7
from academy.adapters.ai.omr.identifier import IdentifierConfigV1, detect_identifier_v1
from academy.adapters.ai.omr.warp import align_to_a4_landscape
from apps.domains.assets.omr.dto.omr_document import OMRDocument
from apps.domains.assets.omr.renderer import html_renderer, pdf_renderer
from apps.domains.assets.omr.renderer.html_renderer import OMRHtmlRenderer
from apps.domains.assets.omr.renderer.pdf_renderer import OMRPdfRenderer
from apps.domains.assets.omr.services.meta_generator import (
    BUB_H,
    MAX_COLS,
    MAX_MC_QUESTIONS,
    MAX_QUESTIONS_PER_COL,
    MIN_VERTICAL_GAP_MM,
    build_mc_column_ranges,
    build_omr_meta,
    compute_safe_layout,
)
from tests.omr.test_omr_full_pipeline import distort
from tests.omr.test_omr_pipeline import create_synthetic_multi_mark_omr, create_synthetic_omr
from tests.omr.test_omr_realuse import id_score, render_marked_pdf, score


SUPPORTED_QUESTION_COUNTS = range(1, MAX_MC_QUESTIONS + 1)
LAYOUT_EDGE_QUESTION_COUNTS = (1, 2, 19, 20, 21, 22, 39, 40, 41, 42, 59, 60)
CLEAR_MULTI_INTENSITY_PAIRS = (
    (0, 95),
    (0, 120),
    (40, 120),
    (70, 130),
    (90, 150),
    (110, 160),
)


def _expected_column_count(question_count: int) -> int:
    if question_count <= MAX_QUESTIONS_PER_COL:
        return 1
    if question_count <= MAX_QUESTIONS_PER_COL * 2:
        return 2
    return MAX_COLS


def _layout_probe_question_numbers(meta: dict) -> list[int]:
    question_count = int(meta["mc_count"])
    per_col = int(meta["layout"]["per_col"])
    n_cols = int(meta["layout"]["n_cols"])
    probes = {1, question_count}

    for col_idx in range(n_cols):
        start = col_idx * per_col + 1
        end = min((col_idx + 1) * per_col, question_count)
        mid = start + max(0, (end - start) // 2)
        for qn in (start, mid, end):
            if 1 <= qn <= question_count:
                probes.add(qn)

    return sorted(probes)


def _all_single_marks(question_count: int) -> dict[str, str]:
    return {
        str(qn): str(((qn - 1) % 5) + 1)
        for qn in range(1, question_count + 1)
    }


def _assert_printed_bubble_outline_matches_meta(image, meta: dict, question_number: int):
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    height, width = gray.shape
    scale_x = width / float(meta["page"]["width"])
    scale_y = height / float(meta["page"]["height"])
    question = meta["questions"][question_number - 1]

    for choice in question["choices"]:
        center_x = int(round(float(choice["center"]["x"]) * scale_x))
        center_y = int(round(float(choice["center"]["y"]) * scale_y))
        radius_x = int(round(float(choice["radius_x"]) * scale_x))
        radius_y = int(round(float(choice["radius_y"]) * scale_y))
        outline_points = (
            (center_x - radius_x, center_y),
            (center_x + radius_x, center_y),
            (center_x, center_y - radius_y),
            (center_x, center_y + radius_y),
        )
        for sample_x, sample_y in outline_points:
            patch = gray[
                max(0, sample_y - 2):min(height, sample_y + 3),
                max(0, sample_x - 2):min(width, sample_x + 3),
            ]
            assert patch.size > 0
            assert int(patch.min()) < 160, (
                question_number,
                choice["label"],
                sample_x,
                sample_y,
                int(patch.min()),
            )


def test_all_supported_question_counts_have_safe_layout_geometry():
    for question_count in SUPPORTED_QUESTION_COUNTS:
        layout = compute_safe_layout(question_count)
        meta = build_omr_meta(question_count=question_count, n_choices=5)
        expected_cols = _expected_column_count(question_count)
        min_center_gap = BUB_H + MIN_VERTICAL_GAP_MM

        assert layout["safe"], (question_count, layout)
        assert meta["layout"]["safe"], (question_count, meta["layout"])
        assert len(meta["questions"]) == question_count
        assert len(meta["columns"]) == expected_cols
        assert meta["layout"]["n_cols"] == expected_cols

        for column in meta["columns"]:
            questions = column["questions"]
            assert 1 <= len(questions) <= MAX_QUESTIONS_PER_COL
            centers_y = [float(q["choices"][0]["center"]["y"]) for q in questions]
            for prev, current in zip(centers_y, centers_y[1:]):
                assert current - prev >= min_center_gap, (
                    question_count,
                    column["column_index"],
                    prev,
                    current,
                )


@pytest.mark.parametrize(
    ("question_count", "expected_ranges"),
    [
        (20, [(1, 20)]),
        (21, [(1, 11), (12, 21)]),
        (40, [(1, 20), (21, 40)]),
        (41, [(1, 14), (15, 28), (29, 41)]),
        (60, [(1, 20), (21, 40), (41, 60)]),
    ],
)
def test_column_ranges_are_shared_by_meta_html_and_renderers(
    question_count: int,
    expected_ranges: list[tuple[int, int]],
    monkeypatch: pytest.MonkeyPatch,
):
    calls: list[tuple[str, int]] = []

    def spy_column_ranges(value: int):
        calls.append(("renderer", value))
        return build_mc_column_ranges(value)

    monkeypatch.setattr(html_renderer, "build_mc_column_ranges", spy_column_ranges)
    monkeypatch.setattr(pdf_renderer, "build_mc_column_ranges", spy_column_ranges)

    direct_ranges = [
        (item["start"], item["end"])
        for item in build_mc_column_ranges(question_count)
    ]
    meta = build_omr_meta(question_count=question_count, n_choices=5)
    meta_ranges = [
        (
            column["questions"][0]["question_number"],
            column["questions"][-1]["question_number"],
        )
        for column in meta["columns"]
    ]
    html_ranges = [
        (column["rows"][0]["number"], column["rows"][-1]["number"])
        for column in OMRHtmlRenderer()._build_mc_columns(  # noqa: SLF001
            OMRDocument(exam_title="Matrix", mc_count=question_count)
        )
    ]
    OMRPdfRenderer().render(OMRDocument(exam_title="Matrix", mc_count=question_count))

    assert direct_ranges == expected_ranges
    assert meta_ranges == expected_ranges
    assert html_ranges == expected_ranges
    assert calls == [("renderer", question_count), ("renderer", question_count)]


def test_engine_detects_blank_single_multi_and_erasure_noise_for_every_supported_count():
    config = AnswerDetectConfig()

    for question_count in SUPPORTED_QUESTION_COUNTS:
        meta = build_omr_meta(question_count=question_count, n_choices=5)

        blank_results = detect_omr_answers_v7(
            image_bgr=create_synthetic_omr(meta, marks={}),
            meta=meta,
            config=config,
        )
        assert all(r.status == "blank" and r.detected == [] for r in blank_results), question_count

        single_marks = _all_single_marks(question_count)
        single_results = detect_omr_answers_v7(
            image_bgr=create_synthetic_omr(meta, marks=single_marks),
            meta=meta,
            config=config,
        )
        assert all(
            r.status == "ok"
            and r.marking == "single"
            and r.detected == [single_marks[str(r.question_id)]]
            for r in single_results
        ), question_count

        probes = _layout_probe_question_numbers(meta)
        clear_multi_marks = {str(qn): {"1": 0, "3": 95} for qn in probes}
        multi_results = detect_omr_answers_v7(
            image_bgr=create_synthetic_multi_mark_omr(meta, marks=clear_multi_marks),
            meta=meta,
            config=config,
        )
        by_question = {r.question_id: r for r in multi_results}
        for qn in probes:
            result = by_question[qn]
            assert result.status == "ok", (question_count, qn, result.to_dict())
            assert result.marking == "multi", (question_count, qn, result.to_dict())
            assert result.detected == ["1", "3"], (question_count, qn, result.to_dict())

        erasure_noise_marks = {str(qn): {"1": 0, "3": 230} for qn in probes}
        erasure_results = detect_omr_answers_v7(
            image_bgr=create_synthetic_multi_mark_omr(meta, marks=erasure_noise_marks),
            meta=meta,
            config=config,
        )
        by_question = {r.question_id: r for r in erasure_results}
        for qn in probes:
            result = by_question[qn]
            assert result.status == "ok", (question_count, qn, result.to_dict())
            assert result.marking == "single", (question_count, qn, result.to_dict())
            assert result.detected == ["1"], (question_count, qn, result.to_dict())


@pytest.mark.parametrize("question_count", LAYOUT_EDGE_QUESTION_COUNTS)
@pytest.mark.parametrize("strong_intensity,weak_intensity", CLEAR_MULTI_INTENSITY_PAIRS)
def test_layout_edges_detect_unbalanced_multi_mark_intensity_matrix(
    question_count: int,
    strong_intensity: int,
    weak_intensity: int,
):
    meta = build_omr_meta(question_count=question_count, n_choices=5)
    probes = _layout_probe_question_numbers(meta)
    marks = {
        str(qn): {"1": strong_intensity, "3": weak_intensity}
        for qn in probes
    }
    results = detect_omr_answers_v7(
        image_bgr=create_synthetic_multi_mark_omr(meta, marks=marks),
        meta=meta,
        config=AnswerDetectConfig(),
    )
    by_question = {r.question_id: r for r in results}

    for qn in probes:
        result = by_question[qn]
        assert result.status == "ok", (question_count, qn, result.to_dict())
        assert result.marking == "multi", (question_count, qn, result.to_dict())
        assert result.detected == ["1", "3"], (question_count, qn, result.to_dict())


@pytest.mark.parametrize("question_count", LAYOUT_EDGE_QUESTION_COUNTS)
@pytest.mark.parametrize(
    ("case_name", "distort_kwargs", "render_kwargs"),
    [
        ("clean", {}, {}),
        ("real_scanner", {"rotation_deg": 1.0, "noise_sigma": 5.0}, {"jpeg_quality": 70, "dpi": 200}),
        ("dpi_150", {}, {"dpi": 150}),
    ],
)
def test_pdf_renderer_to_detector_pipeline_at_layout_edges(
    question_count: int,
    case_name: str,
    distort_kwargs: dict,
    render_kwargs: dict,
):
    meta = build_omr_meta(question_count=question_count, n_choices=5)
    marks: dict[str, object] = _all_single_marks(question_count)
    for qn in _layout_probe_question_numbers(meta):
        marks[str(qn)] = ["1", "3"]
    id_digits = {index: int(value) for index, value in enumerate("12345678")}

    image = render_marked_pdf(meta, marks, id_digits, **render_kwargs)
    dpi = int(render_kwargs.get("dpi", 300))
    scanned = distort(image, dpi=dpi, **distort_kwargs)
    aligned = align_to_a4_landscape(image_bgr=scanned, meta=meta)

    answers = detect_omr_answers_v7(
        image_bgr=aligned.image,
        meta=meta,
        config=AnswerDetectConfig(),
    )
    identifier = detect_identifier_v1(
        image_bgr=aligned.image,
        meta=meta,
        cfg=IdentifierConfigV1(),
    )
    answer_ok, answer_total, wrong = score(answers, marks)
    identifier_ok, identifier_total = id_score(identifier, id_digits)

    assert aligned.success, (question_count, case_name, aligned)
    assert answer_ok == answer_total, (question_count, case_name, wrong[:5])
    assert identifier_ok == identifier_total, (question_count, case_name, identifier)


@pytest.mark.parametrize(
    ("case_name", "distort_kwargs", "render_kwargs"),
    [
        ("clean", {}, {}),
        (
            "real_scanner",
            {"rotation_deg": 1.0, "noise_sigma": 5.0},
            {"jpeg_quality": 70, "dpi": 200},
        ),
        ("dpi_150", {}, {"dpi": 150}),
    ],
)
def test_hidden_optional_essay_area_keeps_objective_recognition_contract(
    case_name: str,
    distort_kwargs: dict,
    render_kwargs: dict,
):
    question_count = 30
    meta = build_omr_meta(question_count=question_count, n_choices=5)
    marks = _all_single_marks(question_count)
    id_digits = {index: int(value) for index, value in enumerate("12345678")}

    image = render_marked_pdf(
        meta,
        marks,
        id_digits,
        include_optional_essay_area=False,
        **render_kwargs,
    )
    dpi = int(render_kwargs.get("dpi", 300))
    scanned = distort(image, dpi=dpi, **distort_kwargs)
    aligned = align_to_a4_landscape(image_bgr=scanned, meta=meta)
    answers = detect_omr_answers_v7(
        image_bgr=aligned.image,
        meta=meta,
        config=AnswerDetectConfig(),
    )
    identifier = detect_identifier_v1(
        image_bgr=aligned.image,
        meta=meta,
        cfg=IdentifierConfigV1(),
    )
    answer_ok, answer_total, wrong = score(answers, marks)
    identifier_ok, identifier_total = id_score(identifier, id_digits)

    assert aligned.success, (case_name, aligned)
    assert answer_ok == answer_total == question_count, (case_name, wrong[:5])
    assert identifier_ok == identifier_total == 8, (case_name, identifier)


def test_hidden_optional_area_printed_bubbles_match_recognition_meta():
    question_count = 30
    meta = build_omr_meta(question_count=question_count, n_choices=5)
    image = render_marked_pdf(
        meta,
        {},
        {},
        include_optional_essay_area=False,
        dpi=300,
    )

    for question_number in _layout_probe_question_numbers(meta):
        _assert_printed_bubble_outline_matches_meta(image, meta, question_number)


@pytest.mark.parametrize(
    ("case_name", "distort_kwargs", "render_kwargs"),
    [
        ("clean", {}, {}),
        (
            "real_scanner",
            {"rotation_deg": 1.0, "noise_sigma": 5.0},
            {"jpeg_quality": 70, "dpi": 200},
        ),
        ("dpi_150", {}, {"dpi": 150}),
    ],
)
def test_twenty_short_answer_only_sheet_aligns_without_objective_rois(
    case_name: str,
    distort_kwargs: dict,
    render_kwargs: dict,
):
    meta = build_omr_meta(question_count=0, essay_count=20, n_choices=5)
    id_digits = {index: int(value) for index, value in enumerate("12345678")}

    image = render_marked_pdf(meta, {}, id_digits, **render_kwargs)
    dpi = int(render_kwargs.get("dpi", 300))
    scanned = distort(image, dpi=dpi, **distort_kwargs)
    aligned = align_to_a4_landscape(image_bgr=scanned, meta=meta)
    answers = detect_omr_answers_v7(
        image_bgr=aligned.image,
        meta=meta,
        config=AnswerDetectConfig(),
    )
    identifier = detect_identifier_v1(
        image_bgr=aligned.image,
        meta=meta,
        cfg=IdentifierConfigV1(),
    )
    identifier_ok, identifier_total = id_score(identifier, id_digits)

    assert aligned.success, (case_name, aligned)
    assert answers == []
    assert identifier_ok == identifier_total == 8, (case_name, identifier)
