"""Real DDL interruption/restart boundaries for the online request index."""
import uuid
import unittest
from unittest.mock import patch

from django.db import IntegrityError, connection
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.recorder import MigrationRecorder
from django.test import TransactionTestCase

from apps.domains.results.tests import test_p0_concurrency_pg as fixtures


class OnlineRequestMigrationPGTests(TransactionTestCase):
    column = ("submissions", "0014_submission_client_request_id")
    index = ("submissions", "0015_submission_request_unique_index")
    index_name = "unique_submission_client_request"

    @classmethod
    def setUpClass(cls):
        if connection.vendor != "postgresql":
            raise unittest.SkipTest("PostgreSQL concurrent DDL is required")
        super().setUpClass()

    def migrate(self, target):
        MigrationExecutor(connection).migrate([target])

    def setUp(self):
        self.migrate(self.column)
        tenant, enrollment, exam = fixtures.TestP0ConcurrencyPG._setup_data(self)
        self.fields = dict(
            tenant=tenant, user=enrollment.student.user, enrollment=enrollment,
            target_type="exam", target_id=exam.pk, source="online", status="FAILED",
            payload={"answers": [{"exam_question_id": 1, "answer": "preserve"}]},
        )
        self.original = fixtures.Submission.objects.create(**self.fields)

    def tearDown(self):
        self.migrate(self.index)
        super().tearDown()

    def index_state(self):
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT c.oid, i.indisvalid, i.indisready, pg_get_indexdef(c.oid) "
                "FROM pg_class c JOIN pg_index i ON i.indexrelid = c.oid "
                "WHERE c.oid = to_regclass(%s)", [self.index_name],
            )
            return cursor.fetchone()

    def assert_interrupted(self):
        applied = MigrationRecorder(connection).applied_migrations()
        self.assertIn(self.column, applied)
        self.assertNotIn(self.index, applied)
        self.original.refresh_from_db()
        self.assertIsNone(self.original.client_request_id)
        self.assertEqual(self.original.payload, self.fields["payload"])

    def assert_recovered(self):
        self.migrate(self.index)
        self.assertIn(self.index, MigrationRecorder(connection).applied_migrations())
        self.assertEqual(self.index_state()[1:3], (True, True))
        self.original.refresh_from_db()
        self.assertEqual(self.original.payload, self.fields["payload"])

    def test_restart_before_index_build_preserves_recorded_column(self):
        editor_class = connection.SchemaEditorClass
        execute = editor_class.execute

        def interrupt(editor, sql, params=()):
            if str(sql).startswith("CREATE UNIQUE INDEX CONCURRENTLY"):
                raise RuntimeError("qa interruption before build")
            return execute(editor, sql, params)

        with patch.object(editor_class, "execute", interrupt):
            with self.assertRaisesRegex(RuntimeError, "qa interruption"):
                self.migrate(self.index)
        self.assertIsNone(self.index_state())
        self.assert_interrupted()
        self.assert_recovered()

    def test_failed_concurrent_build_recreates_only_verified_invalid_index(self):
        key = uuid.uuid4()
        first = fixtures.Submission.objects.create(**self.fields, client_request_id=key)
        second = fixtures.Submission.objects.create(**self.fields, client_request_id=key)
        try:
            with self.assertRaises(IntegrityError):
                self.migrate(self.index)
            failed = self.index_state()
            self.assertIsNotNone(failed)
            self.assertFalse(failed[1])
            self.assert_interrupted()
            # Recovery never repairs canonical data. Only this test's synthetic
            # duplicate is corrected by its owner, preserving both rows/payloads.
            fixtures.Submission.objects.filter(pk=second.pk).update(client_request_id=uuid.uuid4())
            self.assert_recovered()
            self.assertNotEqual(self.index_state()[0], failed[0])
            for row in (first, second):
                row.refresh_from_db()
                self.assertEqual(row.payload, self.fields["payload"])
            self.assertEqual(fixtures.Submission.objects.count(), 3)
        finally:
            fixtures.Submission.objects.filter(pk=second.pk).update(client_request_id=uuid.uuid4())

    def test_restart_after_valid_build_before_recorder_reuses_same_index(self):
        record_applied = MigrationRecorder.record_applied

        def interrupt(recorder, app, name):
            if (app, name) == self.index:
                raise RuntimeError("qa interruption before recorder")
            return record_applied(recorder, app, name)

        with patch.object(MigrationRecorder, "record_applied", interrupt):
            with self.assertRaisesRegex(RuntimeError, "qa interruption"):
                self.migrate(self.index)
        built = self.index_state()
        self.assertEqual(built[1:3], (True, True))
        self.assert_interrupted()
        self.assert_recovered()
        self.assertEqual(self.index_state(), built)

    def test_namesake_with_wrong_definition_is_preserved(self):
        table = connection.ops.quote_name(fixtures.Submission._meta.db_table)
        index = connection.ops.quote_name(self.index_name)
        # Invalid indexes must also have their complete definition verified before
        # removal; these valid namesakes exercise the same fail-closed check.
        for definition in (
            f"CREATE INDEX {index} ON {table} (tenant_id)",
            f"CREATE UNIQUE INDEX {index} ON {table} (tenant_id, enrollment_id, target_type, target_id, client_request_id)",
            f"CREATE UNIQUE INDEX {index} ON {table} (tenant_id DESC, enrollment_id, target_type, target_id, client_request_id) WHERE client_request_id IS NOT NULL",
            f"CREATE UNIQUE INDEX {index} ON {table} (tenant_id, enrollment_id, target_type text_pattern_ops, target_id, client_request_id) WHERE client_request_id IS NOT NULL",
            f'CREATE UNIQUE INDEX {index} ON {table} (tenant_id, enrollment_id, target_type COLLATE "C", target_id, client_request_id) WHERE client_request_id IS NOT NULL',
        ):
            with self.subTest(definition=definition):
                with connection.cursor() as cursor:
                    cursor.execute(definition)
                namesake = self.index_state()
                try:
                    with self.assertRaisesRegex(RuntimeError, "definition mismatch"):
                        self.migrate(self.index)
                    self.assertEqual(self.index_state(), namesake)
                    self.assert_interrupted()
                finally:
                    # Exact synthetic index created by this test, never a runtime
                    # recovery action against an unknown index.
                    with connection.cursor() as cursor:
                        cursor.execute(f"DROP INDEX {index}")
        self.assert_recovered()
