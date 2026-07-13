from __future__ import annotations

from django.apps import apps
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.domains.video.models import Video, VideoFolder
from apps.domains.video.views.video_views import VideoViewSet


User = get_user_model()
Lecture = apps.get_model("lectures", "Lecture")
Session = apps.get_model("lectures", "Session")


class VideoReorderTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = Tenant.objects.create(name="Reorder Tenant", code="video-reorder", is_active=True)
        self.other_tenant = Tenant.objects.create(
            name="Other Reorder Tenant",
            code="video-reorder-other",
            is_active=True,
        )
        self.user = User.objects.create_user(
            username="video_reorder_admin",
            password="test1234",
            tenant=self.tenant,
            is_staff=True,
        )
        TenantMembership.ensure_active(tenant=self.tenant, user=self.user, role="admin")
        lecture = Lecture.get_or_create_system_lecture(self.tenant)
        self.session = Session.objects.create(
            lecture=lecture,
            title="기본 차시",
            order=1,
            regular_order=1,
        )
        other_lecture = Lecture.get_or_create_system_lecture(self.other_tenant)
        other_session = Session.objects.create(
            lecture=other_lecture,
            title="다른 학원 차시",
            order=1,
            regular_order=1,
        )
        self.first = Video.objects.create(
            tenant=self.tenant,
            session=self.session,
            title="First",
            order=1,
        )
        self.second = Video.objects.create(
            tenant=self.tenant,
            session=self.session,
            title="Second",
            order=2,
        )
        self.other = Video.objects.create(
            tenant=self.other_tenant,
            session=other_session,
            title="Other",
            order=9,
        )

    def _reorder(self, items: list[dict]):
        request = self.factory.post("/api/v1/media/videos/reorder/", {"items": items}, format="json")
        request.tenant = self.tenant
        force_authenticate(request, user=self.user)
        return VideoViewSet.as_view({"post": "reorder"})(request)

    def test_reorder_updates_all_items_atomically(self):
        response = self._reorder(
            [
                {"id": self.first.id, "order": 2},
                {"id": self.second.id, "order": 1},
            ]
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data, {"updated": 2})
        self.first.refresh_from_db()
        self.second.refresh_from_db()
        self.assertEqual((self.first.order, self.second.order), (2, 1))

    def test_cross_tenant_item_rejects_entire_reorder(self):
        response = self._reorder(
            [
                {"id": self.first.id, "order": 2},
                {"id": self.other.id, "order": 1},
            ]
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.first.refresh_from_db()
        self.other.refresh_from_db()
        self.assertEqual((self.first.order, self.other.order), (1, 9))

    def test_duplicate_order_rejects_entire_reorder(self):
        response = self._reorder(
            [
                {"id": self.first.id, "order": 1},
                {"id": self.second.id, "order": 1},
            ]
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.first.refresh_from_db()
        self.second.refresh_from_db()
        self.assertEqual((self.first.order, self.second.order), (1, 2))

    def test_mixed_sessions_are_rejected(self):
        lecture = Lecture.get_or_create_system_lecture(self.tenant)
        first_session = Session.objects.create(
            lecture=lecture,
            title="첫 차시",
            order=2,
            regular_order=2,
        )
        second_session = Session.objects.create(
            lecture=lecture,
            title="둘째 차시",
            order=3,
            regular_order=3,
        )
        self.first.session = first_session
        self.first.save(update_fields=["session"])
        self.second.session = second_session
        self.second.save(update_fields=["session"])

        response = self._reorder(
            [
                {"id": self.first.id, "order": 2},
                {"id": self.second.id, "order": 1},
            ]
        )

        self.assertEqual(response.status_code, 400)
        self.first.refresh_from_db()
        self.second.refresh_from_db()
        self.assertEqual((self.first.order, self.second.order), (1, 2))

    def test_partial_reorder_rejects_collision_with_untouched_video(self):
        untouched = Video.objects.create(
            tenant=self.tenant,
            session=self.session,
            title="Untouched",
            order=3,
        )

        response = self._reorder([{"id": self.first.id, "order": 3}])

        self.assertEqual(response.status_code, 409)
        self.first.refresh_from_db()
        untouched.refresh_from_db()
        self.assertEqual((self.first.order, untouched.order), (1, 3))

    def test_orphan_video_is_rejected(self):
        orphan = Video.objects.create(
            tenant=self.tenant,
            title="Orphan",
            order=7,
        )

        response = self._reorder([{"id": orphan.id, "order": 1}])

        self.assertEqual(response.status_code, 400, response.data)
        orphan.refresh_from_db()
        self.assertEqual(orphan.order, 7)

    def test_folder_order_takes_precedence_when_session_is_also_present(self):
        folder = VideoFolder.objects.create(
            tenant=self.tenant,
            session=self.session,
            name="공개 폴더",
        )
        first = Video.objects.create(
            tenant=self.tenant,
            session=self.session,
            folder=folder,
            title="Folder First",
            order=1,
        )
        second = Video.objects.create(
            tenant=self.tenant,
            session=self.session,
            folder=folder,
            title="Folder Second",
            order=2,
        )

        response = self._reorder(
            [
                {"id": first.id, "order": 2},
                {"id": second.id, "order": 1},
            ]
        )

        self.assertEqual(response.status_code, 200, response.data)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual((first.order, second.order), (2, 1))
