"""
P1: resolve_manually / unresolve / waive / carry_over 와
submit/update_exam_retake / submit/update_homework_retake 호출 시
SessionProgress 재계산(progress pipeline)이 on_commit 으로 예약되는지 검증.

테스트 환경은 기본 atomic wrapper 안에서 돌아가므로 on_commit 콜백이 바로
실행되지 않는다. 여기서는 dispatcher.dispatch_progress_pipeline 이 몇 번
호출 예약됐는지를 mock 으로 확인한다.
"""
from unittest.mock import patch

from django.test import TestCase

from apps.domains.clinic.tests import ClinicTestMixin
from apps.domains.exams.models import Exam
from apps.domains.progress.models import ClinicLink
from apps.domains.progress.services.clinic_resolution_service import ClinicResolutionService


PATCH_TARGET = "apps.domains.progress.dispatcher.dispatch_progress_pipeline"


class ResolveDispatchTest(TestCase, ClinicTestMixin):
    def setUp(self):
        self.data = self.setup_full_tenant("rdisp", student_count=1)
        self.tenant = self.data["tenant"]
        self.enrollment = self.data["enrollments"][0]
        self.lec_session = self.data["lec_session"]
        self.exam = Exam.objects.create(
            tenant=self.tenant, title="E", pass_score=60.0, max_score=100.0,
        )
        self.exam.sessions.add(self.lec_session)

    def _make_unresolved_link(self, source_type="exam") -> ClinicLink:
        return ClinicLink.objects.create(
            tenant=self.tenant,
            enrollment=self.enrollment,
            session=self.lec_session,
            reason="AUTO_FAILED",
            source_type=source_type,
            source_id=self.exam.id if source_type == "exam" else 99,
            meta={"kind": "EXAM_FAILED", "exam_id": self.exam.id},
        )

    def test_resolve_manually_dispatches_pipeline(self):
        link = self._make_unresolved_link()
        with patch(PATCH_TARGET) as mock_dispatch:
            with self.captureOnCommitCallbacks(execute=True):
                ClinicResolutionService.resolve_manually(
                    clinic_link_id=link.id, user_id=1, memo="ok",
                )
        mock_dispatch.assert_called_once_with(exam_id=self.exam.id)

    def test_waive_dispatches_pipeline(self):
        link = self._make_unresolved_link()
        with patch(PATCH_TARGET) as mock_dispatch:
            with self.captureOnCommitCallbacks(execute=True):
                ClinicResolutionService.waive(clinic_link_id=link.id, user_id=1)
        mock_dispatch.assert_called_once_with(exam_id=self.exam.id)

    def test_unresolve_dispatches_pipeline(self):
        link = self._make_unresolved_link()
        # 먼저 해소
        with self.captureOnCommitCallbacks(execute=True):
            ClinicResolutionService.resolve_manually(clinic_link_id=link.id, user_id=1)
        link.refresh_from_db()

        with patch(PATCH_TARGET) as mock_dispatch:
            with self.captureOnCommitCallbacks(execute=True):
                ClinicResolutionService.unresolve(clinic_link_id=link.id)
        mock_dispatch.assert_called_once_with(exam_id=self.exam.id)

    def test_carry_over_dispatches_pipeline(self):
        link = self._make_unresolved_link()
        with patch(PATCH_TARGET) as mock_dispatch:
            with self.captureOnCommitCallbacks(execute=True):
                ClinicResolutionService.carry_over(clinic_link_id=link.id)
        mock_dispatch.assert_called_once_with(exam_id=self.exam.id)

    def test_homework_link_dispatches_via_enrollment_session(self):
        """homework 링크도 enrollment+session path 로 dispatch 가 되어야 한다."""
        link = self._make_unresolved_link(source_type="homework")
        with patch(PATCH_TARGET) as mock_dispatch:
            with self.captureOnCommitCallbacks(execute=True):
                ClinicResolutionService.resolve_manually(
                    clinic_link_id=link.id, user_id=1,
                )
        mock_dispatch.assert_called_once_with(
            enrollment_id=self.enrollment.id,
            session_id=self.lec_session.id,
        )

    def test_legacy_link_without_meta_exam_id_uses_enrollment_session(self):
        """source_type=NULL + meta.exam_id 없는 legacy 링크도 dispatch 되어야 한다."""
        link = ClinicLink.objects.create(
            tenant=self.tenant,
            enrollment=self.enrollment,
            session=self.lec_session,
            reason="AUTO_FAILED",
            source_type=None,
            source_id=None,
            meta={},  # 아무 단서 없음
        )
        with patch(PATCH_TARGET) as mock_dispatch:
            with self.captureOnCommitCallbacks(execute=True):
                ClinicResolutionService.waive(clinic_link_id=link.id, user_id=1)
        mock_dispatch.assert_called_once_with(
            enrollment_id=self.enrollment.id,
            session_id=self.lec_session.id,
        )

    def test_legacy_link_with_meta_exam_id_prefers_exam_path(self):
        """source_type=NULL 이어도 meta.exam_id 가 유효하면 exam_id path 우선."""
        link = ClinicLink.objects.create(
            tenant=self.tenant,
            enrollment=self.enrollment,
            session=self.lec_session,
            reason="AUTO_FAILED",
            source_type=None,
            source_id=None,
            meta={"exam_id": self.exam.id},
        )
        with patch(PATCH_TARGET) as mock_dispatch:
            with self.captureOnCommitCallbacks(execute=True):
                ClinicResolutionService.waive(clinic_link_id=link.id, user_id=1)
        mock_dispatch.assert_called_once_with(exam_id=self.exam.id)
