from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.domains.landing_public.api.views.exam_showcase_views import PublicExamShowcaseViewSet
from apps.domains.landing_public.api.views.matchup_showcase_views import PublicMatchupShowcaseViewSet
from apps.domains.landing_public.models import PublicExamShowcase, PublicMatchupShowcase

User = get_user_model()


class PublicMatchupShowcaseVisibilityTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = Tenant.objects.create(name="Showcase", code="showcase-visibility")
        self.owner = User.objects.create_user(
            username="showcase-owner",
            password="pw1234",
            tenant=self.tenant,
            is_staff=True,
        )
        TenantMembership.ensure_active(tenant=self.tenant, user=self.owner, role="owner")

    def _request(self, method: str, path: str, data: dict | None = None, *, staff: bool = False):
        request_method = getattr(self.factory, method)
        request = request_method(path, data or {}, format="json")
        request.tenant = self.tenant
        if staff:
            force_authenticate(request, user=self.owner)
        return request

    def test_public_list_hides_future_published_snapshot(self):
        PublicMatchupShowcase.objects.create(
            tenant=self.tenant,
            title="Future",
            status=PublicMatchupShowcase.Status.PUBLISHED,
            published_at=timezone.now() + timedelta(days=1),
            snapshot_pdf_key="matchup-showcase-snapshots/future.pdf",
            snapshot_at=timezone.now(),
        )
        request = self._request("get", "/api/v1/landing-public/matchup-showcase/")

        response = PublicMatchupShowcaseViewSet.as_view({"get": "list"})(request)

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["count"], 0)
        self.assertEqual(response.data["results"], [])

    def test_public_retrieve_hides_future_published_snapshot(self):
        obj = PublicMatchupShowcase.objects.create(
            tenant=self.tenant,
            title="Future",
            status=PublicMatchupShowcase.Status.PUBLISHED,
            published_at=timezone.now() + timedelta(days=1),
            snapshot_pdf_key="matchup-showcase-snapshots/future.pdf",
            snapshot_at=timezone.now(),
        )
        request = self._request("get", f"/api/v1/landing-public/matchup-showcase/{obj.id}/")

        response = PublicMatchupShowcaseViewSet.as_view({"get": "retrieve"})(request, pk=obj.id)

        self.assertEqual(response.status_code, 404, response.data)

    def test_public_list_hides_future_published_exam_showcase(self):
        PublicExamShowcase.objects.create(
            tenant=self.tenant,
            title="Future Exam",
            status=PublicExamShowcase.Status.PUBLISHED,
            published_at=timezone.now() + timedelta(days=1),
            rows=[{"display_name": "A", "score": 100}],
            summary={"count": 1},
        )
        request = self._request("get", "/api/v1/landing-public/showcase/")

        response = PublicExamShowcaseViewSet.as_view({"get": "list"})(request)

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["count"], 0)
        self.assertEqual(response.data["results"], [])

    def test_public_retrieve_hides_future_published_exam_showcase(self):
        obj = PublicExamShowcase.objects.create(
            tenant=self.tenant,
            title="Future Exam",
            status=PublicExamShowcase.Status.PUBLISHED,
            published_at=timezone.now() + timedelta(days=1),
            rows=[{"display_name": "A", "score": 100}],
            summary={"count": 1},
        )
        request = self._request("get", f"/api/v1/landing-public/showcase/{obj.id}/")

        response = PublicExamShowcaseViewSet.as_view({"get": "retrieve"})(request, pk=obj.id)

        self.assertEqual(response.status_code, 404, response.data)

    def test_patch_cannot_publish_without_snapshot(self):
        obj = PublicMatchupShowcase.objects.create(
            tenant=self.tenant,
            title="Draft without snapshot",
            status=PublicMatchupShowcase.Status.DRAFT,
        )
        request = self._request(
            "patch",
            f"/api/v1/landing-public/matchup-showcase/{obj.id}/",
            {"status": PublicMatchupShowcase.Status.PUBLISHED},
            staff=True,
        )

        response = PublicMatchupShowcaseViewSet.as_view({"patch": "partial_update"})(request, pk=obj.id)

        self.assertEqual(response.status_code, 400, response.data)
        obj.refresh_from_db()
        self.assertEqual(obj.status, PublicMatchupShowcase.Status.DRAFT)

    def test_matchup_patch_rejects_invalid_published_at_without_mutating(self):
        original_until = timezone.now() + timedelta(days=5)
        obj = PublicMatchupShowcase.objects.create(
            tenant=self.tenant,
            title="Scheduled",
            status=PublicMatchupShowcase.Status.PUBLISHED,
            published_at=timezone.now(),
            published_until=original_until,
            snapshot_pdf_key="matchup-showcase-snapshots/scheduled.pdf",
            snapshot_at=timezone.now(),
        )
        request = self._request(
            "patch",
            f"/api/v1/landing-public/matchup-showcase/{obj.id}/",
            {"published_at": "not-a-date", "published_until": ""},
            staff=True,
        )

        response = PublicMatchupShowcaseViewSet.as_view({"patch": "partial_update"})(request, pk=obj.id)

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("published_at", response.data)
        obj.refresh_from_db()
        self.assertIsNotNone(obj.published_at)
        self.assertEqual(obj.published_until, original_until)

    def test_matchup_patch_rejects_invalid_published_until_without_mutating(self):
        original_at = timezone.now()
        obj = PublicMatchupShowcase.objects.create(
            tenant=self.tenant,
            title="Scheduled",
            status=PublicMatchupShowcase.Status.PUBLISHED,
            published_at=original_at,
            published_until=timezone.now() + timedelta(days=5),
            snapshot_pdf_key="matchup-showcase-snapshots/scheduled.pdf",
            snapshot_at=timezone.now(),
        )
        request = self._request(
            "patch",
            f"/api/v1/landing-public/matchup-showcase/{obj.id}/",
            {"published_at": "", "published_until": "not-a-date"},
            staff=True,
        )

        response = PublicMatchupShowcaseViewSet.as_view({"patch": "partial_update"})(request, pk=obj.id)

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("published_until", response.data)
        obj.refresh_from_db()
        self.assertEqual(obj.published_at, original_at)

    def test_cross_tenant_superuser_cannot_preview_private_matchup_showcase(self):
        other_tenant = Tenant.objects.create(name="Other Showcase", code="showcase-other")
        other_superuser = User.objects.create_user(
            username="other-showcase-superuser",
            password="pw1234",
            tenant=other_tenant,
            is_staff=True,
            is_superuser=True,
        )
        obj = PublicMatchupShowcase.objects.create(
            tenant=self.tenant,
            title="Private Draft",
            status=PublicMatchupShowcase.Status.DRAFT,
        )
        request = self.factory.get(f"/api/v1/landing-public/matchup-showcase/{obj.id}/")
        request.tenant = self.tenant
        force_authenticate(request, user=other_superuser)

        response = PublicMatchupShowcaseViewSet.as_view({"get": "retrieve"})(request, pk=obj.id)

        self.assertEqual(response.status_code, 404, response.data)

    def test_cross_tenant_superuser_cannot_preview_private_exam_showcase(self):
        other_tenant = Tenant.objects.create(name="Other Exam Showcase", code="exam-showcase-other")
        other_superuser = User.objects.create_user(
            username="other-exam-showcase-superuser",
            password="pw1234",
            tenant=other_tenant,
            is_staff=True,
            is_superuser=True,
        )
        obj = PublicExamShowcase.objects.create(
            tenant=self.tenant,
            title="Private Exam Draft",
            status=PublicExamShowcase.Status.DRAFT,
            rows=[{"display_name": "A", "score": 100}],
            summary={"count": 1},
        )
        request = self.factory.get(f"/api/v1/landing-public/showcase/{obj.id}/")
        request.tenant = self.tenant
        force_authenticate(request, user=other_superuser)

        response = PublicExamShowcaseViewSet.as_view({"get": "retrieve"})(request, pk=obj.id)

        self.assertEqual(response.status_code, 404, response.data)
