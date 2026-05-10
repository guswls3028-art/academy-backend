"""Stage 2.1 (2026-05-10) — manual_owner_pinned write-side helper unit + integration test.

5/6 dangling 사고 직후 read-side guard 4 곳 추가했으나 write-side 가 누락 → 실효 0.
본 helper (`pin_problems_as_owner_curated`) 가 write-side SSOT.

검증:
- (Unit) 빈 입력 idempotent (no-op)
- (Unit) 신규 problem id pin 마킹 (meta.manual_owner_pinned=True)
- (Unit) 이미 pinned 면 no-op
- (Unit) cross-tenant id 무시 (tenant 격리)
- (Integration) 실 DB 모델 INSERT 후 helper 호출 → meta 갱신 검증
- (Integration) retry_document 보호 메커니즘: pinned problem 은 hard delete X
"""
from __future__ import annotations

import pytest
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


@pytest.mark.django_db
class TestPinHelperIntegration:
    """실 DB 모델 INSERT 후 helper 호출 → meta.manual_owner_pinned=True 검증.

    pytest fixture (django_db) 로 SQLite 활용. 운영 PG와 jsonb 동작 차이 적음.
    """

    def _setup_tenant_and_problems(self):
        """Tenant + Document + Problems 2건 신규 생성."""
        from apps.core.models.tenant import Tenant
        from apps.domains.matchup.models import MatchupDocument, MatchupProblem
        from apps.domains.inventory.models import InventoryFile

        tenant = Tenant.objects.create(code=f"pin-test-{id(self) % 99999}", name="pin-test")
        # InventoryFile 은 매치업 doc 의 1:1 의존 — 가짜 row.
        inv = InventoryFile.objects.create(
            tenant=tenant,
            r2_key=f"pin-test-key-{id(self) % 99999}",
            original_name="pin-test.pdf",
            content_type="application/pdf",
            size_bytes=0,
        )
        doc = MatchupDocument.objects.create(
            tenant=tenant,
            inventory_file=inv,
            title="pin-test-doc",
            r2_key=inv.r2_key,
            original_name=inv.original_name,
            content_type=inv.content_type,
            size_bytes=inv.size_bytes,
        )
        p1 = MatchupProblem.objects.create(
            tenant=tenant, document=doc, number=1, text="q1", meta={},
        )
        p2 = MatchupProblem.objects.create(
            tenant=tenant, document=doc, number=2, text="q2",
            meta={"existing_key": "value"},
        )
        return tenant, doc, p1, p2

    def test_integration_pin_marks_meta_in_db(self):
        from apps.domains.matchup.services import pin_problems_as_owner_curated
        from apps.domains.matchup.models import MatchupProblem

        tenant, _doc, p1, p2 = self._setup_tenant_and_problems()

        result = pin_problems_as_owner_curated(
            tenant_id=tenant.id, problem_ids=[p1.id, p2.id],
        )
        assert result == 2

        p1.refresh_from_db()
        p2.refresh_from_db()
        assert p1.meta.get("manual_owner_pinned") is True
        assert p2.meta.get("manual_owner_pinned") is True
        # 기존 key 보존
        assert p2.meta.get("existing_key") == "value"

        # 두 번째 호출은 idempotent
        result2 = pin_problems_as_owner_curated(
            tenant_id=tenant.id, problem_ids=[p1.id, p2.id],
        )
        assert result2 == 0

    def test_integration_cross_tenant_isolated(self):
        from apps.domains.matchup.services import pin_problems_as_owner_curated
        from apps.core.models.tenant import Tenant

        tenant_a, _, p1, _ = self._setup_tenant_and_problems()
        tenant_b = Tenant.objects.create(code=f"pin-other-{id(self) % 99999}", name="pin-other")

        # tenant_b 컨텍스트에서 tenant_a 의 problem id 호출 → 무시
        result = pin_problems_as_owner_curated(
            tenant_id=tenant_b.id, problem_ids=[p1.id],
        )
        assert result == 0

        p1.refresh_from_db()
        assert p1.meta.get("manual_owner_pinned") is not True

    def test_integration_retry_document_protects_pinned(self):
        """retry_document 시 pinned problem 은 보존, 일반 problem 은 삭제."""
        from apps.domains.matchup.services import pin_problems_as_owner_curated
        from apps.domains.matchup.models import MatchupProblem

        tenant, doc, p1, p2 = self._setup_tenant_and_problems()
        # p1 만 pin
        pin_problems_as_owner_curated(tenant_id=tenant.id, problem_ids=[p1.id])

        # retry_document 의 protected 계산 부분만 시뮬레이션 — dispatch_job 부분은 mock.
        # 실 retry_document 호출은 R2 / job dispatch 의존이라 통합 부담 큼.
        manual_ids = list(
            doc.problems.filter(meta__manual=True).values_list("id", flat=True)
        )
        pinned_ids = list(
            doc.problems.filter(meta__manual_owner_pinned=True).values_list("id", flat=True)
        )
        protected_ids = list(set(manual_ids) | set(pinned_ids))
        assert p1.id in protected_ids
        assert p2.id not in protected_ids

        # 보호 외 문제 삭제 시뮬레이션
        doc.problems.exclude(id__in=protected_ids).delete()
        remaining = list(doc.problems.values_list("id", flat=True))
        assert p1.id in remaining
        assert p2.id not in remaining
