from types import SimpleNamespace

import pytest

from apps.domains.matchup.views_hit_report import (
    _HIT_REPORT_CANDIDATE_BATCH_LIMIT,
    _filter_hit_report_candidate_exam_problems,
    _parse_hit_report_candidate_problem_ids,
)


def test_parse_candidate_problem_ids_dedupes_and_preserves_order():
    assert _parse_hit_report_candidate_problem_ids(" 3,1,3;2,, ") == [3, 1, 2]


@pytest.mark.parametrize("raw", ["abc", "1,-2", "0", "1,2,x"])
def test_parse_candidate_problem_ids_rejects_invalid_values(raw):
    with pytest.raises(ValueError):
        _parse_hit_report_candidate_problem_ids(raw)


def test_parse_candidate_problem_ids_rejects_oversized_batches():
    raw = ",".join(str(i) for i in range(1, _HIT_REPORT_CANDIDATE_BATCH_LIMIT + 2))

    with pytest.raises(ValueError):
        _parse_hit_report_candidate_problem_ids(raw)


def test_filter_candidate_exam_problems_preserves_document_order():
    problems = [SimpleNamespace(id=10), SimpleNamespace(id=20), SimpleNamespace(id=30)]

    assert _filter_hit_report_candidate_exam_problems(problems, [30, 10]) == [
        problems[0],
        problems[2],
    ]
