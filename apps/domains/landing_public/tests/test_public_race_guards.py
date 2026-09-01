from __future__ import annotations

import inspect
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db.utils import IntegrityError
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.domains.landing_public.api.views.board_views import PublicBoardPostViewSet
from apps.domains.landing_public.api.views.report_views import PublicReportViewSet
from apps.domains.landing_public.models import PublicBoardPost, PublicPostLike
from apps.domains.students.test_support import create_student_fixture


User = get_user_model()


class LandingPublicRaceGuardTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = Tenant.objects.create(
            name="Landing Race",
            code="landing-race",
            is_active=True,
        )
        self.user = User.objects.create_user(
            username="landing-race-member",
            password="test1234",
            tenant=self.tenant,
        )
        TenantMembership.ensure_active(tenant=self.tenant, user=self.user, role="student")
        create_student_fixture(
            tenant=self.tenant,
            user=self.user,
            name="Member",
            ps_number="LANDING-RACE-1",
        )
        self.board = PublicBoardPost.objects.create(
            tenant=self.tenant,
            author=self.user,
            author_display_name="Member",
            author_role="student",
            title="Race board",
            content="content",
            status=PublicBoardPost.Status.PUBLISHED,
            external_visible=True,
        )

    def _post_board_like(self):
        request = self.factory.post(f"/landing-public/board/{self.board.id}/like/", {}, format="json")
        request.tenant = self.tenant
        force_authenticate(request, user=self.user)
        return PublicBoardPostViewSet.as_view({"post": "like_toggle"})(request, pk=self.board.id)

    def test_like_toggle_resolves_concurrent_create_integrity_error_as_liked(self):
        with patch(
            "apps.domains.landing_public.api.views.like_toggle.PublicPostLike.objects.create",
            side_effect=IntegrityError("duplicate like"),
        ):
            response = self._post_board_like()

        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data["liked"])

    def test_like_toggle_existing_like_deletes_without_counter_underflow(self):
        PublicPostLike.objects.create(
            tenant=self.tenant,
            user=self.user,
            target_kind=PublicPostLike.TargetKind.BOARD,
            target_id=self.board.id,
        )
        self.board.refresh_from_db()
        self.assertEqual(self.board.like_count, 1)

        response = self._post_board_like()

        self.assertEqual(response.status_code, 200, response.data)
        self.assertFalse(response.data["liked"])
        self.board.refresh_from_db()
        self.assertEqual(self.board.like_count, 0)

    def test_report_create_locks_target_before_duplicate_check(self):
        source = inspect.getsource(PublicReportViewSet.create)

        self.assertIn("transaction.atomic", source)
        self.assertIn("select_for_update", source)

    def test_board_get_is_read_only_and_post_view_records_open(self):
        self.board.author = User.objects.create_user(
            username="landing-race-author",
            password="test1234",
            tenant=self.tenant,
        )
        self.board.save(update_fields=["author"])
        get_request = self.factory.get(f"/landing-public/board/{self.board.id}/")
        get_request.tenant = self.tenant

        response = PublicBoardPostViewSet.as_view({"get": "retrieve"})(
            get_request,
            pk=self.board.id,
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.board.refresh_from_db()
        self.assertEqual(self.board.view_count, 0)

        post_request = self.factory.post(f"/landing-public/board/{self.board.id}/view/")
        post_request.tenant = self.tenant
        view_response = PublicBoardPostViewSet.as_view({"post": "record_view"})(
            post_request,
            pk=self.board.id,
        )

        self.assertEqual(view_response.status_code, 200, view_response.data)
        self.board.refresh_from_db()
        self.assertEqual(self.board.view_count, 1)
