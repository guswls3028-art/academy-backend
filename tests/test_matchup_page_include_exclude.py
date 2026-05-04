"""excluded_pages 롤백 (include) + 기존 exclude 회귀 테스트.

P1 (2026-05-04): 학원장이 실수로 페이지 제외했다가 복구하는 case 안전망.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


def _make_doc(meta=None):
    """MatchupDocument mock — meta dict + .save() 호출 추적."""
    doc = MagicMock()
    doc.meta = meta or {}
    doc.problems.all.return_value = []  # exclude는 problems 순회 — empty list
    saved = []

    def fake_save(update_fields=None):
        saved.append(dict(doc.meta))

    doc.save.side_effect = fake_save
    doc._saved_versions = saved
    return doc


def test_include_page_removes_from_excluded_list():
    """이미 excluded 페이지를 include하면 리스트에서 제거 + requires_reanalyze=True."""
    from apps.domains.matchup.services import include_page_to_matchup

    doc = _make_doc(meta={"excluded_pages": [1, 3, 5]})
    result = include_page_to_matchup(doc, 3)

    assert result["excluded_pages"] == [1, 5]
    assert result["requires_reanalyze"] is True
    assert doc.meta["excluded_pages"] == [1, 5]
    assert len(doc._saved_versions) == 1  # save 호출됨


def test_include_page_not_excluded_no_op():
    """제외 안 된 페이지를 include 시도하면 no-op + requires_reanalyze=False."""
    from apps.domains.matchup.services import include_page_to_matchup

    doc = _make_doc(meta={"excluded_pages": [1, 3]})
    result = include_page_to_matchup(doc, 7)

    assert result["excluded_pages"] == [1, 3]
    assert result["requires_reanalyze"] is False
    assert len(doc._saved_versions) == 0  # save 미호출 (no-op)


def test_include_page_empty_excluded_list_no_op():
    """excluded_pages 자체 비어있으면 no-op."""
    from apps.domains.matchup.services import include_page_to_matchup

    doc = _make_doc(meta={})
    result = include_page_to_matchup(doc, 0)

    assert result["excluded_pages"] == []
    assert result["requires_reanalyze"] is False


def test_include_page_invalid_index_raises():
    """page_index 범위 벗어나면 ValueError (exclude와 동일 검증)."""
    from apps.domains.matchup.services import include_page_to_matchup

    doc = _make_doc(meta={"excluded_pages": [0]})
    with pytest.raises(ValueError, match="page_index"):
        include_page_to_matchup(doc, -1)
    with pytest.raises(ValueError, match="page_index"):
        include_page_to_matchup(doc, 1000)


def test_include_page_meta_preserved():
    """include 후 meta 다른 키들은 보존."""
    from apps.domains.matchup.services import include_page_to_matchup

    doc = _make_doc(meta={
        "excluded_pages": [2, 4],
        "source_type": "academy_workbook",
        "page_image_keys": ["key0", "key1"],
    })
    include_page_to_matchup(doc, 2)

    # meta의 다른 키들 그대로
    assert doc.meta["source_type"] == "academy_workbook"
    assert doc.meta["page_image_keys"] == ["key0", "key1"]
    assert doc.meta["excluded_pages"] == [4]


def test_include_page_int_str_normalize():
    """str 또는 int page_index 모두 정상 처리."""
    from apps.domains.matchup.services import include_page_to_matchup

    doc = _make_doc(meta={"excluded_pages": [1, 2, 3]})
    # int
    result = include_page_to_matchup(doc, 2)
    assert 2 not in result["excluded_pages"]


def test_exclude_then_include_round_trip():
    """exclude 후 include로 원래 상태로 (excluded_pages 동일)."""
    from apps.domains.matchup.services import (
        exclude_page_from_matchup,
        include_page_to_matchup,
    )

    doc = _make_doc(meta={})
    # exclude는 problems 순회하므로 mock 추가
    doc.problems.all.return_value = []  # no problems → removed=0

    exclude_result = exclude_page_from_matchup(doc, 5)
    assert exclude_result["excluded_pages"] == [5]

    include_result = include_page_to_matchup(doc, 5)
    assert include_result["excluded_pages"] == []
    assert include_result["requires_reanalyze"] is True
