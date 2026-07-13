import importlib
from types import SimpleNamespace
from unittest.mock import MagicMock

from django.apps import apps
from django.db import IntegrityError, transaction
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from apps.core.models import Tenant
from apps.domains.video.models import Video, VideoFolder


Lecture = apps.get_model("lectures", "Lecture")
Session = apps.get_model("lectures", "Session")


class VideoOrderMigrationDriftTests(SimpleTestCase):
    def _schema_editor(self, constraints):
        cursor = MagicMock()
        connection = MagicMock()
        connection.cursor.return_value.__enter__.return_value = cursor
        connection.introspection.get_constraints.return_value = constraints
        schema_editor = MagicMock()
        schema_editor.connection = connection
        return schema_editor

    def _historical_apps(self, name):
        constraint = SimpleNamespace(name=name)
        model = SimpleNamespace(
            _meta=SimpleNamespace(
                constraints=[constraint],
                db_table="video_videofolder",
            )
        )
        historical_apps = MagicMock()
        historical_apps.get_model.return_value = model
        return historical_apps, model, constraint

    def test_missing_legacy_constraint_is_a_safe_noop(self):
        migration = importlib.import_module(
            "apps.domains.video.migrations.0019_video_order_and_folder_uniqueness"
        )
        historical_apps, _, _ = self._historical_apps(
            migration.LEGACY_FOLDER_CONSTRAINT
        )
        schema_editor = self._schema_editor({})

        migration.remove_legacy_folder_constraint_if_present(
            historical_apps,
            schema_editor,
        )

        schema_editor.remove_constraint.assert_not_called()

    def test_existing_legacy_constraint_is_removed(self):
        migration = importlib.import_module(
            "apps.domains.video.migrations.0019_video_order_and_folder_uniqueness"
        )
        name = migration.LEGACY_FOLDER_CONSTRAINT
        historical_apps, model, constraint = self._historical_apps(name)
        schema_editor = self._schema_editor({name: {"unique": True}})

        migration.remove_legacy_folder_constraint_if_present(
            historical_apps,
            schema_editor,
        )

        schema_editor.remove_constraint.assert_called_once_with(model, constraint)


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
