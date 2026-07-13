from django.apps import apps
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from apps.core.models import Tenant
from apps.domains.video.models import Video, VideoFolder


Lecture = apps.get_model("lectures", "Lecture")
Session = apps.get_model("lectures", "Session")


class VideoDatabaseUniquenessTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            name="Video DB Guard",
            code="video-db-guard",
            is_active=True,
        )
        lecture = Lecture.get_or_create_system_lecture(self.tenant)
        self.session = Session.objects.create(
            lecture=lecture,
            title="DB guard session",
            order=1,
            regular_order=1,
        )

    def test_active_session_video_order_must_be_unique(self):
        Video.objects.create(
            tenant=self.tenant,
            session=self.session,
            title="First",
            order=1,
        )

        with self.assertRaises(IntegrityError), transaction.atomic():
            Video.objects.create(
                tenant=self.tenant,
                session=self.session,
                title="Duplicate",
                order=1,
            )

    def test_active_folder_video_order_must_be_unique(self):
        folder = VideoFolder.objects.create(tenant=self.tenant, name="Folder")
        Video.objects.create(
            tenant=self.tenant,
            session=self.session,
            folder=folder,
            title="First",
            order=1,
        )

        with self.assertRaises(IntegrityError), transaction.atomic():
            Video.objects.create(
                tenant=self.tenant,
                session=self.session,
                folder=folder,
                title="Duplicate",
                order=1,
            )

    def test_soft_deleted_video_does_not_reserve_order(self):
        deleted = Video.objects.create(
            tenant=self.tenant,
            session=self.session,
            title="Deleted",
            order=1,
        )
        Video.all_with_deleted.filter(pk=deleted.pk).update(deleted_at=timezone.now())

        replacement = Video.objects.create(
            tenant=self.tenant,
            session=self.session,
            title="Replacement",
            order=1,
        )

        self.assertIsNotNone(replacement.pk)

    def test_root_folder_name_must_be_unique_per_tenant(self):
        VideoFolder.objects.create(tenant=self.tenant, name="Root")

        with self.assertRaises(IntegrityError), transaction.atomic():
            VideoFolder.objects.create(tenant=self.tenant, name="Root")

    def test_folder_must_have_a_tenant(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            VideoFolder.objects.create(name="Tenantless")

    def test_child_folder_name_must_be_unique_per_parent(self):
        parent = VideoFolder.objects.create(tenant=self.tenant, name="Parent")
        VideoFolder.objects.create(tenant=self.tenant, parent=parent, name="Child")

        with self.assertRaises(IntegrityError), transaction.atomic():
            VideoFolder.objects.create(
                tenant=self.tenant,
                parent=parent,
                name="Child",
            )

    def test_same_folder_name_is_allowed_under_different_parents(self):
        first_parent = VideoFolder.objects.create(tenant=self.tenant, name="First")
        second_parent = VideoFolder.objects.create(tenant=self.tenant, name="Second")

        first = VideoFolder.objects.create(
            tenant=self.tenant,
            parent=first_parent,
            name="Shared",
        )
        second = VideoFolder.objects.create(
            tenant=self.tenant,
            parent=second_parent,
            name="Shared",
        )

        self.assertNotEqual(first.pk, second.pk)
