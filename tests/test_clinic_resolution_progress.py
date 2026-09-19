"""Clinic decisions preserve student results and bound progress work to one learner."""
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase

from apps.core.models import TenantMembership
from apps.domains.clinic.tests import ClinicTestMixin
from apps.domains.exams.models import Exam, ExamEnrollment
from apps.domains.progress.models import ClinicLink, ProgressPolicy, SessionProgress
from apps.domains.progress.services.clinic_resolution_service import ClinicResolutionService
from apps.domains.progress.services.session_calculator import SessionProgressCalculator
from apps.domains.results.models import Result
from apps.domains.results.services.student_result_service import get_my_exam_result_data


class ResolveProgressPersistenceTest(TestCase, ClinicTestMixin):
    def setUp(self):
        self.data = self.setup_full_tenant("resolve_scope", student_count=2)
        self.session = self.data["lec_session"]
        self.enrollment, self.other_enrollment = self.data["enrollments"]
        TenantMembership.ensure_active(
            tenant=self.data["tenant"], user=self.data["students"][0].user, role="student",
        )
        self.exam = Exam.objects.create(
            tenant=self.data["tenant"], title="Shared exam",
            exam_type=Exam.ExamType.REGULAR, pass_score=60, max_score=100,
        )
        self.exam.sessions.add(self.session)
        ProgressPolicy.objects.create(
            lecture=self.data["lecture"], exam_start_session_order=1,
        )
        for enrollment in self.data["enrollments"]:
            ExamEnrollment.objects.create(exam=self.exam, enrollment=enrollment)
            Result.objects.create(
                enrollment=enrollment, target_type="exam", target_id=self.exam.id,
                total_score=40, max_score=100,
            )
            self.make_clinic_link(enrollment, self.session, source_id=self.exam.id)
            SessionProgress.objects.create(
                enrollment=enrollment, session=self.session,
                exam_aggregate_score=17, video_progress_rate=72,
                homework_submitted=True,
            )
        self.link = ClinicLink.objects.get(enrollment=self.enrollment, session=self.session)

    def test_pass_and_undo_persist_without_recalculating_another_student(self):
        other_before = SessionProgress.objects.filter(enrollment=self.other_enrollment).values().get()
        request = SimpleNamespace(
            tenant=self.data["tenant"], user=self.data["students"][0].user, META={},
        )
        for resolved in (True, False):
            with self.subTest(resolved=resolved), patch(
                "apps.domains.progress.services.clinic_resolution_service._send_resolution_notification"
            ), patch.object(
                SessionProgressCalculator, "calculate", wraps=SessionProgressCalculator.calculate,
            ) as calculate, self.captureOnCommitCallbacks(execute=True):
                if resolved:
                    ClinicResolutionService.resolve_manually(clinic_link_id=self.link.id, memo="확인 완료")
                else:
                    ClinicResolutionService.unresolve(clinic_link_id=self.link.id)

            self.assertEqual(calculate.call_count, 1)
            self.link.refresh_from_db()
            self.assertEqual(self.link.resolved_at is not None, resolved)
            progress = SessionProgress.objects.get(enrollment=self.enrollment, session=self.session)
            self.assertEqual(progress.exam_aggregate_score, 40)
            self.assertEqual(progress.video_progress_rate, 72)
            self.assertTrue(progress.homework_submitted)
            self.assertEqual(
                SessionProgress.objects.filter(enrollment=self.other_enrollment).values().get(),
                other_before,
            )
            student_result = get_my_exam_result_data(request, self.exam.id, tenant=request.tenant)
            self.assertEqual(student_result["remediated"], resolved)
            self.assertEqual(student_result["clinic_required"], not resolved)
            self.assertEqual(Result.objects.get(enrollment=self.enrollment).total_score, 40)
