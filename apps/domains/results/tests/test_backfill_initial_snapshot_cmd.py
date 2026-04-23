"""
backfill_initial_snapshot management command 실전 시나리오 테스트.
"""
from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from apps.domains.clinic.tests import ClinicTestMixin
from apps.domains.exams.models import Exam
from apps.domains.results.models import ExamAttempt, Result


class BackfillInitialSnapshotCmdTest(TestCase, ClinicTestMixin):
    def setUp(self):
        self.data = self.setup_full_tenant("bsnap", student_count=1)
        self.tenant = self.data["tenant"]
        self.enrollment = self.data["enrollments"][0]
        self.lec_session = self.data["lec_session"]
        self.exam = Exam.objects.create(
            tenant=self.tenant, title="E", pass_score=60.0, max_score=100.0,
        )
        self.exam.sessions.add(self.lec_session)

    def _run(self, *args) -> str:
        out = StringIO()
        call_command("backfill_initial_snapshot", *args, stdout=out, verbosity=1)
        return out.getvalue()

    def test_fills_missing_snapshot_from_result(self):
        a1 = ExamAttempt.objects.create(
            exam=self.exam, enrollment=self.enrollment,
            attempt_index=1, is_representative=True, status="done",
            submission_id=0,
        )
        Result.objects.create(
            target_type="exam", target_id=self.exam.id,
            enrollment=self.enrollment, total_score=80, max_score=100,
            attempt=a1,
        )

        out = self._run()
        self.assertIn("updated=1", out)

        a1.refresh_from_db()
        self.assertIsInstance(a1.meta, dict)
        self.assertIn("initial_snapshot", a1.meta)
        snap = a1.meta["initial_snapshot"]
        self.assertEqual(snap["total_score"], 80)
        self.assertEqual(snap["max_score"], 100)
        self.assertEqual(snap["source"], "legacy_backfill_cli")

    def test_dry_run_does_not_modify(self):
        a1 = ExamAttempt.objects.create(
            exam=self.exam, enrollment=self.enrollment,
            attempt_index=1, is_representative=True, status="done",
            submission_id=0,
        )
        Result.objects.create(
            target_type="exam", target_id=self.exam.id,
            enrollment=self.enrollment, total_score=70, max_score=100,
            attempt=a1,
        )

        out = self._run("--dry-run")
        self.assertIn("No changes committed", out)

        a1.refresh_from_db()
        self.assertFalse(isinstance(a1.meta, dict) and "initial_snapshot" in a1.meta)

    def test_at_risk_marker_when_retake_exists(self):
        a1 = ExamAttempt.objects.create(
            exam=self.exam, enrollment=self.enrollment,
            attempt_index=1, is_representative=False, status="done",
            submission_id=0,
        )
        Result.objects.create(
            target_type="exam", target_id=self.exam.id,
            enrollment=self.enrollment, total_score=45, max_score=100,
            attempt=a1,
        )
        ExamAttempt.objects.create(
            exam=self.exam, enrollment=self.enrollment,
            attempt_index=2, is_representative=True, status="done",
            submission_id=None, is_retake=True,
        )

        self._run()
        a1.refresh_from_db()
        snap = a1.meta["initial_snapshot"]
        self.assertEqual(snap.get("_warning"), "possibly_overwritten_by_retake")

    def test_only_at_risk_filter_skips_safe_rows(self):
        a1 = ExamAttempt.objects.create(
            exam=self.exam, enrollment=self.enrollment,
            attempt_index=1, is_representative=True, status="done",
            submission_id=0,
        )
        Result.objects.create(
            target_type="exam", target_id=self.exam.id,
            enrollment=self.enrollment, total_score=90, max_score=100,
            attempt=a1,
        )

        out = self._run("--only-at-risk")
        self.assertIn("updated=0", out)

        a1.refresh_from_db()
        self.assertFalse(isinstance(a1.meta, dict) and "initial_snapshot" in a1.meta)

    def test_preserves_existing_snapshot(self):
        a1 = ExamAttempt.objects.create(
            exam=self.exam, enrollment=self.enrollment,
            attempt_index=1, is_representative=True, status="done",
            submission_id=0,
            meta={"initial_snapshot": {"total_score": 42, "source": "original"}},
        )
        Result.objects.create(
            target_type="exam", target_id=self.exam.id,
            enrollment=self.enrollment, total_score=80, max_score=100,
            attempt=a1,
        )

        self._run()
        a1.refresh_from_db()
        self.assertEqual(a1.meta["initial_snapshot"]["total_score"], 42)
        self.assertEqual(a1.meta["initial_snapshot"]["source"], "original")

    def test_tenant_scope_limits_rows(self):
        other = self.setup_full_tenant("bsnap_other", student_count=1)
        other_tenant = other["tenant"]
        other_enr = other["enrollments"][0]
        other_session = other["lec_session"]
        other_exam = Exam.objects.create(
            tenant=other_tenant, title="E2", pass_score=60.0, max_score=100.0,
        )
        other_exam.sessions.add(other_session)

        a_mine = ExamAttempt.objects.create(
            exam=self.exam, enrollment=self.enrollment,
            attempt_index=1, is_representative=True, status="done", submission_id=0,
        )
        Result.objects.create(
            target_type="exam", target_id=self.exam.id,
            enrollment=self.enrollment, total_score=80, max_score=100, attempt=a_mine,
        )
        a_other = ExamAttempt.objects.create(
            exam=other_exam, enrollment=other_enr,
            attempt_index=1, is_representative=True, status="done", submission_id=0,
        )
        Result.objects.create(
            target_type="exam", target_id=other_exam.id,
            enrollment=other_enr, total_score=50, max_score=100, attempt=a_other,
        )

        self._run("--tenant", str(self.tenant.id))

        a_mine.refresh_from_db()
        a_other.refresh_from_db()

        self.assertIn("initial_snapshot", a_mine.meta or {})
        self.assertFalse(
            isinstance(a_other.meta, dict) and "initial_snapshot" in a_other.meta
        )
