"""Compatibility with earlier file decisions and the enrollment deletion path in rollback images."""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import IntegrityError, connection, migrations, models, transaction
from django.db.models.deletion import get_candidate_relations_to_delete
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.recorder import MigrationRecorder
from django.test import TransactionTestCase
from django.utils import timezone

from apps.core.models import Tenant
from apps.domains.enrollment.services.lifecycle import delete_enrollment
from apps.domains.enrollment.test_support import create_enrollment_fixture
from apps.domains.homework_results.test_support import create_homework_fixture
from apps.domains.lectures.test_support import create_lecture_fixture, create_session_fixture
from apps.domains.students.test_support import create_student_fixture


class PreviousCandidateMigration(migrations.Migration):
    """The two operations shipped in the earlier 0018 candidate only."""

    dependencies = [("progress", "0017_cliniclink_grading_retracted_resolution")]
    operations = [
        migrations.AddField(
            model_name="assessmentcorrection",
            name="accepted_media_fingerprint",
            field=models.CharField(blank=True, max_length=64, null=True),
        ),
        migrations.AddField(
            model_name="assessmentcorrection",
            name="media_acceptance_updated_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]


class HomeworkFileDecisionMigrationTests(TransactionTestCase):
    previous = ("progress", "0017_cliniclink_grading_retracted_resolution")
    old_candidate = ("progress", "0018_homework_file_acceptance")
    decision = ("progress", "0018_homework_file_decision")

    def setUp(self):
        executor = MigrationExecutor(connection)
        self.original_targets = executor.loader.graph.leaf_nodes()
        self.addCleanup(self.restore_schema)
        self.before_state = executor.migrate([self.previous])
        self.old = PreviousCandidateMigration(self.old_candidate[1], self.old_candidate[0])
        candidate_state = executor.apply_migration(self.before_state.clone(), self.old)
        self.Correction = candidate_state.apps.get_model("progress", "AssessmentCorrection")

        tenant = Tenant.objects.create(code="file-decision-migration", name="File Decision Migration")
        user = get_user_model().objects.create_user(username="file-decision-migration", tenant=tenant)
        student = create_student_fixture(tenant=tenant, user=user, ps_number="File01", omr_code="12345678", name="Student")
        lecture = create_lecture_fixture(tenant=tenant, title="Lecture")
        self.session = create_session_fixture(lecture=lecture, order=1, title="Session")
        self.enrollment = create_enrollment_fixture(tenant=tenant, student=student, lecture=lecture)
        self.correction = self.Correction.objects.create(
            tenant_id=tenant.pk, enrollment_id=self.enrollment.pk, session_id=self.session.pk,
            source_type="homework", source_id=123, completed=False, note="기존 수기 미완료", updated_by_id=None,
        )

    def restore_schema(self):
        if not hasattr(self, "old"):
            return
        executor = MigrationExecutor(connection)
        applied = MigrationRecorder(connection).applied_migrations()
        if self.decision in applied:
            executor.migrate([self.previous])
        if self.old_candidate in applied:
            MigrationExecutor(connection).unapply_migration(self.before_state.clone(), self.old)
        MigrationExecutor(connection).migrate(self.original_targets)

    def migrate_decision(self):
        return MigrationExecutor(connection).migrate([self.decision])

    def assert_manual_review_preserved(self):
        row = self.Correction.objects.get(pk=self.correction.pk)
        self.assertFalse(row.completed)
        self.assertEqual(row.note, self.correction.note)
        self.assertIsNone(row.updated_by_id)
        self.assertEqual(row.updated_at, self.correction.updated_at)
        return row

    def test_idle_candidate_allows_new_table_and_keeps_manual_decision(self):
        state = self.migrate_decision()
        applied = MigrationRecorder(connection).applied_migrations()
        self.assertIn(self.old_candidate, applied)
        self.assertIn(self.decision, applied)
        Decision = state.apps.get_model("progress", "HomeworkFileAcceptance")
        self.assertIn(Decision._meta.db_table, connection.introspection.table_names())
        self.assertEqual(Decision.objects.count(), 0)
        row = self.assert_manual_review_preserved()
        self.assertIsNone(row.accepted_media_fingerprint)
        self.assertIsNone(row.media_acceptance_updated_at)

    def test_candidate_file_pass_or_cancellation_stops_before_any_changes(self):
        decision_table = "progress_homeworkfileacceptance"
        for fingerprint, updated_at in (("a" * 64, timezone.now()), (None, timezone.now())):
            with self.subTest(accepted=bool(fingerprint)):
                self.Correction.objects.filter(pk=self.correction.pk).update(
                    accepted_media_fingerprint=fingerprint, media_acceptance_updated_at=updated_at,
                )
                with self.assertRaisesRegex(RuntimeError, "Unreleased homework file decisions exist"):
                    self.migrate_decision()
                applied = MigrationRecorder(connection).applied_migrations()
                self.assertIn(self.old_candidate, applied)
                self.assertNotIn(self.decision, applied)
                self.assertNotIn(decision_table, connection.introspection.table_names())
                row = self.assert_manual_review_preserved()
                self.assertEqual(row.accepted_media_fingerprint, fingerprint)
                self.assertEqual(row.media_acceptance_updated_at, updated_at)

    def test_old_enrollment_delete_after_new_file_decisions(self):
        state = self.migrate_decision()
        Decision = state.apps.get_model("progress", "HomeworkFileAcceptance")
        tenant = self.enrollment.tenant
        other_user = get_user_model().objects.create_user(tenant=tenant, username="other-file-decision")
        other_student = create_student_fixture(
            tenant=tenant, user=other_user, ps_number="File02", omr_code="12345679", name="Other",
        )
        other = create_enrollment_fixture(tenant=tenant, student=other_student, lecture=self.enrollment.lecture)
        for index, fingerprint in enumerate(("a" * 64, None)):
            homework = create_homework_fixture(tenant=tenant, session=self.session, title=f"Review {index}")
            for enrollment in (self.enrollment, other):
                Decision.objects.create(
                    tenant_id=tenant.pk, enrollment_id=enrollment.pk, session_id=self.session.pk,
                    homework_id=homework.pk, fingerprint=fingerprint,
                )
        before = list(Decision.objects.order_by("pk").values())
        MigrationExecutor(connection).migrate(self.original_targets)
        self.assertEqual(list(Decision.objects.order_by("pk").values()), before)
        if connection.vendor == "postgresql":
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT a.attname, c.confdeltype, c.condeferrable, c.condeferred, c.convalidated "
                    "FROM pg_constraint c JOIN pg_attribute a "
                    "ON a.attrelid = c.conrelid AND a.attnum = c.conkey[1] "
                    "WHERE c.contype = 'f' AND c.conrelid = %s::regclass",
                    [Decision._meta.db_table],
                )
                self.assertCountEqual(cursor.fetchall(), [
                    (column, "c", True, True, True)
                    for column in ("tenant_id", "enrollment_id", "session_id", "homework_id")
                ])

        def old_relations(options):
            # The rolling/rollback image knows every existing relation except the newly added model.
            return (
                rel for rel in get_candidate_relations_to_delete(options)
                if rel.related_model._meta.label_lower != "progress.homeworkfileacceptance"
            )

        with patch("django.db.models.deletion.get_candidate_relations_to_delete", old_relations), transaction.atomic():
            delete_enrollment(self.enrollment)
        self.assertEqual(
            list(Decision.objects.order_by("pk").values()),
            [row for row in before if row["enrollment_id"] == other.pk],
        )
        connection.check_constraints()
        surviving_ids = {
            "tenant_id": tenant.pk, "enrollment_id": other.pk,
            "session_id": self.session.pk, "homework_id": homework.pk,
        }
        for field in surviving_ids:
            with self.subTest(missing_parent=field), self.assertRaises(IntegrityError), transaction.atomic():
                Decision.objects.create(**{**surviving_ids, field: -1})
