"""Stage 2.1 (2026-05-10) — manual_owner_pinned write-side helper unit test.

5/6 dangling 사고 직후 read-side guard 4 곳 추가했으나 write-side 가 누락 → 실효 0.
본 helper (`pin_problems_as_owner_curated`) 가 write-side SSOT.

검증:
- 빈 입력 idempotent (no-op)
- 신규 problem id pin 마킹 (meta.manual_owner_pinned=True)
- 이미 pinned 면 no-op
- cross-tenant id 무시 (tenant 격리)
- 호출자가 트랜잭션 안에서 묶을 수 있도록 signal/save 호출 패턴 정상
"""
from __future__ import annotations

from unittest import TestCase
from unittest.mock import MagicMock, patch


class PinHelperTests(TestCase):
    """pin_problems_as_owner_curated 단위 검증 (mock 기반 — DB 없음)."""

    def test_empty_problem_ids_returns_zero(self):
        from apps.domains.matchup.services import pin_problems_as_owner_curated

        result = pin_problems_as_owner_curated(tenant_id=1, problem_ids=[])
        self.assertEqual(result, 0)

    def test_pins_unmarked_problems(self):
        from apps.domains.matchup.services import pin_problems_as_owner_curated

        # 가짜 problem 인스턴스 — meta 비어있음
        p1 = MagicMock()
        p1.id = 100
        p1.meta = {}
        p1.save = MagicMock()
        p2 = MagicMock()
        p2.id = 101
        p2.meta = {"existing_key": "value"}
        p2.save = MagicMock()

        # MatchupProblem.objects.filter(...).only(...) 가 [p1, p2] 반환하도록
        qs = MagicMock()
        qs.__iter__ = lambda self: iter([p1, p2])
        objects = MagicMock()
        objects.filter.return_value.only.return_value = qs

        with patch("apps.domains.matchup.services.MatchupProblem.objects", objects):
            result = pin_problems_as_owner_curated(
                tenant_id=1, problem_ids=[100, 101],
            )

        self.assertEqual(result, 2)
        # 두 problem 모두 manual_owner_pinned=True 마킹
        self.assertTrue(p1.meta.get("manual_owner_pinned"))
        self.assertTrue(p2.meta.get("manual_owner_pinned"))
        # 기존 key 보존
        self.assertEqual(p2.meta.get("existing_key"), "value")
        # save 호출 — update_fields 명시
        p1.save.assert_called_once()
        p2.save.assert_called_once()
        for call in [p1.save.call_args, p2.save.call_args]:
            self.assertIn("meta", call.kwargs.get("update_fields", []))

    def test_already_pinned_is_noop(self):
        from apps.domains.matchup.services import pin_problems_as_owner_curated

        p = MagicMock()
        p.id = 200
        p.meta = {"manual_owner_pinned": True}
        p.save = MagicMock()

        qs = MagicMock()
        qs.__iter__ = lambda self: iter([p])
        objects = MagicMock()
        objects.filter.return_value.only.return_value = qs

        with patch("apps.domains.matchup.services.MatchupProblem.objects", objects):
            result = pin_problems_as_owner_curated(
                tenant_id=1, problem_ids=[200],
            )

        self.assertEqual(result, 0)
        p.save.assert_not_called()

    def test_tenant_isolation_passed_to_filter(self):
        """tenant_id 가 .filter(tenant_id=...) 로 전달되는지 확인."""
        from apps.domains.matchup.services import pin_problems_as_owner_curated

        objects = MagicMock()
        objects.filter.return_value.only.return_value = []

        with patch("apps.domains.matchup.services.MatchupProblem.objects", objects):
            pin_problems_as_owner_curated(tenant_id=42, problem_ids=[1, 2, 3])

        objects.filter.assert_called_once()
        kwargs = objects.filter.call_args.kwargs
        self.assertEqual(kwargs.get("tenant_id"), 42)
        # id__in 도 전달
        self.assertEqual(set(kwargs.get("id__in", [])), {1, 2, 3})
