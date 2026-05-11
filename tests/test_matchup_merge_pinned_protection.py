"""merge_problems P0 보호 회귀 락 (2026-05-11).

others 안에 manual_owner_pinned=True problem 이 있으면 merge 차단 — primary 만
살리고 others 삭제하면 적중보고서 selected_problem_ids 가 dead pid 가리키는
dangling 발생 (project_matchup_hitreport_dangling_recovery_2026_05_06 사고 클래스).

primary 자신이 pinned 인 case 는 허용 — PID 보존되므로 dangling 0.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


def _make_merge_problem(*, pid, number, pinned=False, manual=False):
    p = MagicMock()
    p.id = pid
    p.number = number
    p.image_key = f"k/{pid}.png"
    p.text = ""
    p.meta = {
        **({"manual": True} if manual else {}),
        **({"manual_owner_pinned": True} if pinned else {}),
    }
    return p


def _patch_problem_qs(problems):
    """MatchupProblem.objects.filter 결과를 problems 로 iterate 하도록 mock."""
    fake_qs = MagicMock()
    fake_qs.__iter__ = lambda self: iter(problems)
    return fake_qs


def test_merge_problems_rejects_pinned_others():
    """others 안에 pinned 가 있으면 ValueError + 학원장 안내 메시지."""
    from apps.domains.matchup.services import merge_problems

    document = MagicMock()
    document.tenant = MagicMock()

    primary = _make_merge_problem(pid=10, number=1)
    pinned_other = _make_merge_problem(pid=11, number=2, pinned=True)

    with patch("apps.domains.matchup.services.MatchupProblem") as MockProblem:
        MockProblem.objects.filter.return_value = _patch_problem_qs([primary, pinned_other])
        with pytest.raises(ValueError, match=r"별 표시"):
            merge_problems(document, problem_ids=[10, 11])


def test_merge_problems_primary_pinned_allowed():
    """primary 자신이 pinned 면 허용 — PID 보존되므로 dangling 0.

    검증 포인트: pinned check 단계에서 ValueError raise 안 됨 (이후 image fetch
    단계에서 mock 미설정으로 다른 예외가 나오는 것은 OK — pinned 차단 통과 확인).
    """
    from apps.domains.matchup.services import merge_problems

    document = MagicMock()
    document.tenant = MagicMock()

    primary = _make_merge_problem(pid=10, number=1, pinned=True)
    other = _make_merge_problem(pid=11, number=2)

    with patch("apps.domains.matchup.services.MatchupProblem") as MockProblem:
        MockProblem.objects.filter.return_value = _patch_problem_qs([primary, other])
        # pinned check 단계 통과 → image fetch 등 후속 실패는 무시 (다른 예외 OK)
        with pytest.raises(Exception) as exc_info:
            merge_problems(document, problem_ids=[10, 11])
        # 핵심 회귀 표지 — pinned 차단 메시지면 fail (primary pinned 거짓 차단됨)
        assert "별 표시" not in str(exc_info.value)


def test_merge_problems_no_pinned_passes_check():
    """pinned 없으면 차단 단계 통과 — image fetch 단계로 진행."""
    from apps.domains.matchup.services import merge_problems

    document = MagicMock()
    document.tenant = MagicMock()

    p1 = _make_merge_problem(pid=10, number=1)
    p2 = _make_merge_problem(pid=11, number=2)

    with patch("apps.domains.matchup.services.MatchupProblem") as MockProblem:
        MockProblem.objects.filter.return_value = _patch_problem_qs([p1, p2])
        with pytest.raises(Exception) as exc_info:
            merge_problems(document, problem_ids=[10, 11])
        assert "별 표시" not in str(exc_info.value)
