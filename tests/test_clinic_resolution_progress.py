"""Clinic decisions preserve student results and bound progress work to one learner."""
from types import SimpleNamespace
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import TenantMembership
from apps.domains.clinic.tests import ClinicTestMixin
from apps.domains.exams.models import Exam, ExamEnrollment
from apps.domains.progress.models import ClinicLink, ProgressPolicy, SessionProgress
from apps.domains.progress.services.clinic_resolution_service import ClinicResolutionService
from apps.domains.progress.services.session_calculator import SessionProgressCalculator
from apps.domains.progress.views import ClinicLinkViewSet
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
                    ClinicResolutionService.unresolve(
                        clinic_link_id=self.link.id, expected_resolved_at=self.link.resolved_at,
                    )

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

    def _undo(self, payload, *, user=None, tenant=None):
        if user is None:
            user = self.make_user("undo_staff")
            TenantMembership.ensure_active(tenant=self.data["tenant"], user=user, role="staff")
        request = APIRequestFactory().post("/unresolve/", payload, format="json")
        request.tenant = tenant or self.data["tenant"]
        force_authenticate(request, user=user)
        return ClinicLinkViewSet.as_view({"post": "unresolve"})(request, pk=self.link.pk)

    def _mark_manual(self):
        ClinicResolutionService.resolve_manually(clinic_link_id=self.link.id, memo="수동 확인")
        self.link.refresh_from_db()
        return self.link.resolved_at.isoformat()

    def test_staff_can_undo_exact_manual_decision_once_and_preserve_history(self):
        expected = self._mark_manual()
        with patch(
            "apps.domains.progress.services.clinic_resolution_service._dispatch_progress_for_link"
        ) as dispatch:
            response = self._undo({"expected_resolved_at": expected})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertIsNone(response.data["resolved_at"])
        dispatch.assert_called_once()
        self.link.refresh_from_db()
        self.assertEqual(self.link.resolution_history[-1]["prev_evidence"]["memo"], "수동 확인")
        repeat = self._undo({"expected_resolved_at": expected}, user=self.data["students"][0].user)
        self.assertEqual(repeat.status_code, 403)
        staff = self.make_user("repeat_staff")
        TenantMembership.ensure_active(tenant=self.data["tenant"], user=staff, role="staff")
        with patch(
            "apps.domains.progress.services.clinic_resolution_service._dispatch_progress_for_link"
        ) as dispatch:
            repeat = self._undo({"expected_resolved_at": expected}, user=staff)
        self.assertEqual(repeat.status_code, 409)
        dispatch.assert_not_called()

    def test_conditional_undo_rejects_changed_automatic_and_corrected_decisions(self):
        expected = self._mark_manual()
        staff = self.make_user("conflict_staff")
        TenantMembership.ensure_active(tenant=self.data["tenant"], user=staff, role="staff")
        scenarios = [
            {"resolved_at": timezone.now() + timedelta(seconds=1)},
            {"resolution_type": ClinicLink.ResolutionType.EXAM_PASS},
            {"resolution_type": ClinicLink.ResolutionType.HOMEWORK_PASS},
            {"resolution_evidence": {"assessment_correction_id": 12}},
            {"resolved_at": None},
        ]
        original = {"resolved_at": self.link.resolved_at,
                    "resolution_type": ClinicLink.ResolutionType.MANUAL_OVERRIDE,
                    "resolution_evidence": self.link.resolution_evidence}
        for changed in scenarios:
            with self.subTest(changed=changed):
                ClinicLink.objects.filter(pk=self.link.pk).update(**(original | changed))
                before = ClinicLink.objects.filter(pk=self.link.pk).values().get()
                with patch(
                    "apps.domains.progress.services.clinic_resolution_service._dispatch_progress_for_link"
                ) as dispatch:
                    response = self._undo({"expected_resolved_at": expected}, user=staff)
                self.assertEqual(response.status_code, 409, response.data)
                self.assertEqual(response.data["code"], "clinic_resolution_conflict")
                self.assertEqual(ClinicLink.objects.filter(pk=self.link.pk).values().get(), before)
                dispatch.assert_not_called()

    def test_undo_token_validation_and_tenant_boundary_preserve_decision(self):
        expected = self._mark_manual()
        staff = self.make_user("boundary_staff")
        TenantMembership.ensure_active(tenant=self.data["tenant"], user=staff, role="staff")
        for invalid in (None, "not-a-date", ""):
            with self.subTest(invalid=invalid):
                response = self._undo({"expected_resolved_at": invalid}, user=staff)
                self.assertEqual(response.status_code, 400, response.data)
        other_tenant = self.make_tenant("undo_other")
        TenantMembership.ensure_active(tenant=other_tenant, user=staff, role="staff")
        response = self._undo({"expected_resolved_at": expected}, user=staff, tenant=other_tenant)
        self.assertEqual(response.status_code, 404)
        self.link.refresh_from_db()
        self.assertEqual(self.link.resolved_at.isoformat(), expected)
