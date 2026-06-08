from __future__ import annotations

import pytest

from academy.adapters.ai.omr.engine import AnswerDetectConfig, detect_omr_answers_v7
from academy.adapters.ai.omr.identifier import IdentifierConfigV1, detect_identifier_v1
from academy.adapters.ai.omr.warp import align_to_a4_landscape
from apps.domains.assets.omr.services.meta_generator import (
    BUB_H,
    MAX_COLS,
    MAX_MC_QUESTIONS,
    MAX_QUESTIONS_PER_COL,
    MIN_VERTICAL_GAP_MM,
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
