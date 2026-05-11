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


# ── P0 보호 회귀 락 (2026-05-11) ─────────────────────────────────
#
# 매치업 도메인 dangling 사고 클래스 차단:
#   1. 자동 problem 이 적중보고서 selected_problem_ids 로 별 토글 (manual_owner_pinned=True)
#   2. 학원장이 해당 페이지를 실수로 exclude 하면 pinned problem 삭제 → dead pid 만 가리키는 dangling
# 참조: project_matchup_hitreport_dangling_recovery_2026_05_06 사고 메모리.


def _make_problem(*, pid, page_index, manual=False, pinned=False):
    """MatchupProblem mock — meta 만 사용."""
    p = MagicMock()
    p.id = pid
    p.meta = {
        "page_index": page_index,
        **({"manual": True} if manual else {}),
        **({"manual_owner_pinned": True} if pinned else {}),
    }
    return p


def test_exclude_page_preserves_manual_owner_pinned():
    """manual_owner_pinned=True 자동 problem 은 페이지 exclude 에서도 보존.

    실패하면: 적중보고서 selected_problem_ids 가 dead pid 가리키는 dangling 재발.
    """
    from apps.domains.matchup.services import exclude_page_from_matchup

    doc = _make_doc(meta={})
    pinned = _make_problem(pid=1, page_index=2, pinned=True)
    auto = _make_problem(pid=2, page_index=2)
    other_page = _make_problem(pid=3, page_index=5)  # 다른 페이지 — 영향 X
    doc.problems.all.return_value = [pinned, auto, other_page]

    with patch("apps.domains.matchup.services.delete_problem_with_r2") as mock_del:
        result = exclude_page_from_matchup(doc, 2)

    assert result["removed_problems"] == 1
    assert result["preserved_pinned"] == 1
    assert result["preserved_manual"] == 0
    # auto 만 삭제 — pinned 와 다른 페이지는 보존
    mock_del.assert_called_once_with(auto)


def test_exclude_page_preserves_manual_and_pinned_both():
    """manual=True 와 manual_owner_pinned=True 둘 다 보호 — 정책 일관."""
    from apps.domains.matchup.services import exclude_page_from_matchup

    doc = _make_doc(meta={})
    manual = _make_problem(pid=1, page_index=2, manual=True)
    pinned = _make_problem(pid=2, page_index=2, pinned=True)
    both = _make_problem(pid=3, page_index=2, manual=True, pinned=True)
    auto = _make_problem(pid=4, page_index=2)
    doc.problems.all.return_value = [manual, pinned, both, auto]

    with patch("apps.domains.matchup.services.delete_problem_with_r2") as mock_del:
        result = exclude_page_from_matchup(doc, 2)

    assert result["removed_problems"] == 1  # auto 만 삭제
    assert result["preserved_manual"] == 2  # manual + both (manual 카운트)
    assert result["preserved_pinned"] == 1  # pinned 만 (manual 겹침 제외)
    mock_del.assert_called_once_with(auto)
