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

import json
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
            scope="admin",
            student_ps="",
            display_name="pin-test.pdf",
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

    def test_skeleton_insert_preserves_manual_and_pinned(self):
        """worker skeleton insert must not delete manual/pinned problem rows."""
        from academy.application.use_cases.ai.pipelines.matchup_pipeline import (
            _insert_skeleton_problems,
        )
        from apps.domains.matchup.models import MatchupProblem

        tenant, doc, manual, pinned = self._setup_tenant_and_problems()
        manual.meta = {"manual": True}
        manual.save(update_fields=["meta"])
        pinned.meta = {"manual_owner_pinned": True}
        pinned.save(update_fields=["meta"])
        auto = MatchupProblem.objects.create(
            tenant=tenant,
            document=doc,
            number=3,
            text="auto",
            meta={},
        )

        _insert_skeleton_problems(
            [
                {"number": 1, "page_index": 0, "bbox": {"x": 0, "y": 0, "w": 1, "h": 1}},
                {"number": 2, "page_index": 0, "bbox": {"x": 1, "y": 0, "w": 1, "h": 1}},
                {"number": 3, "page_index": 0, "bbox": {"x": 2, "y": 0, "w": 1, "h": 1}},
                {"number": 4, "page_index": 0, "bbox": {"x": 3, "y": 0, "w": 1, "h": 1}},
            ],
            document_id=str(doc.id),
            tenant_id=str(tenant.id),
            job_id="skeleton-preserve-test",
        )

        remaining_ids = set(
            MatchupProblem.objects.filter(document=doc).values_list("id", flat=True)
        )
        assert manual.id in remaining_ids
        assert pinned.id in remaining_ids
        assert auto.id not in remaining_ids
        assert MatchupProblem.objects.filter(
            document=doc,
            number=4,
            meta__is_partial=True,
        ).exists()

    def test_manual_crop_preserves_owner_pinned_meta_on_recut(self, tmp_path):
        """같은 번호 재크롭은 이미지/분리 메타만 갱신하고 학원장 pin은 보존한다."""
        from PIL import Image
        from apps.domains.matchup.services import manually_crop_problem

        tenant, doc, pinned, _auto = self._setup_tenant_and_problems()
        inv = doc.inventory_file
        inv.original_name = "pin-test.png"
        inv.content_type = "image/png"
        inv.save(update_fields=["original_name", "content_type", "updated_at"])
        doc.original_name = inv.original_name
        doc.content_type = inv.content_type
        doc.save(update_fields=["original_name", "content_type", "updated_at"])

        pinned.meta = {
            "manual_owner_pinned": True,
            "format": "essay",
            "legacy_note": "keep",
            "bbox_norm": [0.0, 0.0, 0.2, 0.2],
            "is_partial": True,
            "number_mismatch": {"expected": 1, "actual": 7},
            "low_quality": True,
            "proposal_status": "pending",
        }
        pinned.save(update_fields=["meta"])

        page_path = tmp_path / "page.png"
        Image.new("RGB", (120, 100), color="white").save(page_path)

        with (
            patch(
                "apps.domains.matchup.services._download_inventory_to_temp",
                return_value=str(page_path),
            ),
            patch("apps.infrastructure.storage.r2.upload_fileobj_to_r2_storage"),
            patch("apps.domains.matchup.services._enqueue_manual_problem_index"),
            patch("apps.domains.matchup.services._record_manual_correction_delta"),
            patch("apps.domains.matchup.services._record_layout_fingerprint"),
            patch("apps.domains.matchup.cache.invalidate_tenant_similar_cache"),
        ):
            problem = manually_crop_problem(
                doc,
                page_index=0,
                bbox_norm=(0.1, 0.2, 0.5, 0.4),
                number=pinned.number,
                text="recut",
            )

        assert problem.id == pinned.id
        problem.refresh_from_db()
        meta = problem.meta
        assert meta["manual_owner_pinned"] is True
        assert meta["manual"] is True
        assert meta["format"] == "essay"
        assert meta["legacy_note"] == "keep"
        assert meta["bbox_norm"] == [0.1, 0.2, 0.5, 0.4]
        assert "paste" not in meta
        assert "is_partial" not in meta
        assert "number_mismatch" not in meta
        assert "low_quality" not in meta
        assert "proposal_status" not in meta

    def test_paste_image_preserves_owner_pinned_meta_on_replace(self):
        """붙여넣기 재등록도 기존 pinned 보호 메타를 잃지 않는다."""
        import io
        from PIL import Image
        from apps.domains.matchup.services import paste_image_as_problem

        _tenant, doc, pinned, _auto = self._setup_tenant_and_problems()
        pinned.meta = {
            "manual_owner_pinned": True,
            "format": "essay",
            "legacy_note": "keep",
            "bbox_norm": [0.0, 0.0, 0.2, 0.2],
            "is_partial": True,
            "processing_quality": "failed",
        }
        pinned.save(update_fields=["meta"])

        image_buf = io.BytesIO()
        Image.new("RGB", (32, 32), color="white").save(image_buf, "PNG")

        with (
            patch("apps.infrastructure.storage.r2.upload_fileobj_to_r2_storage"),
            patch("apps.domains.matchup.services._enqueue_manual_problem_index"),
            patch("apps.domains.matchup.cache.invalidate_tenant_similar_cache"),
        ):
            problem = paste_image_as_problem(
                doc,
                image_bytes=image_buf.getvalue(),
                content_type="image/png",
                number=pinned.number,
            )

        assert problem.id == pinned.id
        problem.refresh_from_db()
        meta = problem.meta
        assert meta["manual_owner_pinned"] is True
        assert meta["manual"] is True
        assert meta["paste"] is True
        assert meta["page_index"] == 0
        assert meta["format"] == "essay"
        assert meta["legacy_note"] == "keep"
        assert "bbox_norm" not in meta
        assert "is_partial" not in meta
        assert "processing_quality" not in meta

    def _staff_user(self, tenant):
        from django.contrib.auth import get_user_model
        from apps.core.models import TenantMembership

        user = get_user_model().objects.create_user(
            username=f"pin-staff-{id(self)}",
            password="test1234",
            tenant=tenant,
        )
        TenantMembership.ensure_active(tenant=tenant, user=user, role="teacher")
        return user

    def _request(self, method: str, path: str, *, tenant, user, body: dict | None = None):
        from rest_framework.test import APIRequestFactory

        factory = APIRequestFactory()
        if method == "delete":
            request = factory.delete(path)
        else:
            request = factory.post(
                path,
                data=json.dumps(body or {}),
                content_type="application/json",
            )
        request.tenant = tenant
        return request, patch(
            "apps.domains.matchup.views.JWTAuthentication.authenticate",
            return_value=(user, None),
        )

    def test_single_problem_delete_rejects_owner_pinned_problem(self):
        from apps.domains.matchup.models import MatchupProblem
        from apps.domains.matchup.views import ProblemDetailView

        tenant, _doc, pinned, _auto = self._setup_tenant_and_problems()
        pinned.meta = {"manual_owner_pinned": True}
        pinned.save(update_fields=["meta"])
        user = self._staff_user(tenant)
        request, auth = self._request(
            "delete",
            f"/api/v1/matchup/problems/{pinned.id}/",
            tenant=tenant,
            user=user,
        )

        with auth, patch("apps.domains.matchup.views.delete_problem_with_r2") as deleter:
            response = ProblemDetailView.as_view()(request, problem_id=pinned.id)

        assert response.status_code == 409
        deleter.assert_not_called()
        assert MatchupProblem.objects.filter(id=pinned.id).exists()

    def test_bulk_delete_preserves_owner_pinned_problem(self):
        from apps.domains.matchup.models import MatchupProblem
        from apps.domains.matchup.views import DocumentBulkDeleteProblemsView

        tenant, doc, pinned, auto = self._setup_tenant_and_problems()
        pinned.meta = {"manual_owner_pinned": True}
        pinned.save(update_fields=["meta"])
        user = self._staff_user(tenant)
        request, auth = self._request(
            "post",
            f"/api/v1/matchup/documents/{doc.id}/bulk-delete-problems/",
            tenant=tenant,
            user=user,
            body={"problem_ids": [pinned.id, auto.id]},
        )

        with auth, patch("apps.domains.matchup.views.delete_problem_with_r2", side_effect=lambda p: p.delete()):
            response = DocumentBulkDeleteProblemsView.as_view()(request, doc_id=doc.id)

        payload = json.loads(response.content)
        assert response.status_code == 200
        assert payload["deleted"] == 1
        assert payload["preserved_protected"] == 1
        assert MatchupProblem.objects.filter(id=pinned.id).exists()
        assert not MatchupProblem.objects.filter(id=auto.id).exists()

    def test_document_delete_rejects_document_with_owner_pinned_problem(self):
        from apps.domains.matchup.models import MatchupDocument
        from apps.domains.matchup.views import DocumentDetailView

        tenant, doc, pinned, _auto = self._setup_tenant_and_problems()
        pinned.meta = {"manual_owner_pinned": True}
        pinned.save(update_fields=["meta"])
        user = self._staff_user(tenant)
        request, auth = self._request(
            "delete",
            f"/api/v1/matchup/documents/{doc.id}/",
            tenant=tenant,
            user=user,
        )

        with auth, patch("apps.domains.matchup.views.delete_document_with_r2") as deleter:
            response = DocumentDetailView.as_view()(request, doc_id=doc.id)

        assert response.status_code == 409
        deleter.assert_not_called()
        assert MatchupDocument.objects.filter(id=doc.id).exists()
