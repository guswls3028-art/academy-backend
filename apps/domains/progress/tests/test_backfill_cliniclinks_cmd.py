"""
backfill_legacy_cliniclinks + detect_clinic_drift 시나리오 테스트.
"""
from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from apps.domains.clinic.tests import ClinicTestMixin
from apps.domains.exams.models import Exam
from apps.domains.progress.models import ClinicLink


class BackfillLegacyClinicLinksTest(TestCase, ClinicTestMixin):
    def setUp(self):
        self.data = self.setup_full_tenant("blcl", student_count=1)
        self.tenant = self.data["tenant"]
        self.enrollment = self.data["enrollments"][0]
        self.lec_session = self.data["lec_session"]
        self.exam = Exam.objects.create(
            tenant=self.tenant, title="E", pass_score=60.0, max_score=100.0,
        )
        self.exam.sessions.add(self.lec_session)

    def _run(self, *args) -> str:
        out = StringIO()
        call_command("backfill_legacy_cliniclinks", *args, stdout=out, verbosity=1)
        return out.getvalue()

    def test_rule1_meta_exam_id(self):
        link = ClinicLink.objects.create(
            tenant=self.tenant, enrollment=self.enrollment, session=self.lec_session,
            reason="AUTO_FAILED", source_type=None, source_id=None,
            meta={"exam_id": self.exam.id, "kind": "EXAM_FAILED"},
        )
        out = self._run()
        self.assertIn("rule1_meta_exam_id", out)
        self.assertIn("updated=1", out)

        link.refresh_from_db()
        self.assertEqual(link.source_type, "exam")
        self.assertEqual(link.source_id, self.exam.id)
        # history append
        self.assertTrue(any(
            h.get("action") == "legacy_source_backfill"
            for h in (link.resolution_history or [])
        ))

    def test_rule3_session_single_exam(self):
        # 세션에 exam 이 하나뿐이면 kind=EXAM_FAILED 만으로도 추론
        link = ClinicLink.objects.create(
            tenant=self.tenant, enrollment=self.enrollment, session=self.lec_session,
            reason="AUTO_FAILED", source_type=None, source_id=None,
            meta={"kind": "EXAM_FAILED"},  # exam_id 없음
        )
        out = self._run()
        self.assertIn("rule3_session_single_exam", out)
        link.refresh_from_db()
        self.assertEqual(link.source_type, "exam")
        self.assertEqual(link.source_id, self.exam.id)

    def test_skip_when_no_inference(self):
        link = ClinicLink.objects.create(
            tenant=self.tenant, enrollment=self.enrollment, session=self.lec_session,
            reason="AUTO_FAILED", source_type=None, source_id=None,
            meta={},  # 아무 단서 없음
        )
        # 그리고 세션에 exam 2개 붙여 rule3 도 불가능한 상황
        Exam.objects.create(
            tenant=self.tenant, title="E2", pass_score=60.0, max_score=100.0,
        ).sessions.add(self.lec_session)

        out = self._run()
        self.assertIn("no_inference_possible", out)
        link.refresh_from_db()
        self.assertIsNone(link.source_type)

    def test_dry_run(self):
        link = ClinicLink.objects.create(
            tenant=self.tenant, enrollment=self.enrollment, session=self.lec_session,
            reason="AUTO_FAILED", source_type=None, source_id=None,
            meta={"exam_id": self.exam.id},
        )
        self._run("--dry-run")
        link.refresh_from_db()
        self.assertIsNone(link.source_type)

    def test_report_only(self):
        ClinicLink.objects.create(
            tenant=self.tenant, enrollment=self.enrollment, session=self.lec_session,
            reason="AUTO_FAILED", source_type=None, source_id=None,
            meta={"exam_id": self.exam.id},
        )
        out = self._run("--report-only")
        self.assertIn("rule1_meta_exam_id", out)
        # 실제 업데이트 안 됨
        self.assertTrue(ClinicLink.objects.filter(source_type__isnull=True).exists())


class DetectClinicDriftTest(TestCase, ClinicTestMixin):
    def setUp(self):
        self.data = self.setup_full_tenant("dcd", student_count=1)
        self.tenant = self.data["tenant"]
        self.enrollment = self.data["enrollments"][0]
        self.lec_session = self.data["lec_session"]
        self.exam = Exam.objects.create(
            tenant=self.tenant, title="E", pass_score=60.0, max_score=100.0,
        )
        self.exam.sessions.add(self.lec_session)

    def _run(self, *args) -> str:
        out = StringIO()
        call_command("detect_clinic_drift", *args, stdout=out, verbosity=1)
        return out.getvalue()

    def test_drift_report_runs_without_error(self):
        # legacy link 1개
        ClinicLink.objects.create(
            tenant=self.tenant, enrollment=self.enrollment, session=self.lec_session,
            reason="AUTO_FAILED", source_type=None, source_id=None, meta={},
        )
        out = self._run()
        # 핵심 섹션 존재 확인
        self.assertIn("Legacy ClinicLink", out)
        self.assertIn("missing meta.initial_snapshot", out)
        self.assertIn("SessionProgress", out)
        self.assertIn("CARRIED_OVER count", out)
        self.assertIn("No data was modified", out)
