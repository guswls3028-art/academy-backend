"""
Contract test: admin_student_grades_view.achievement 와
compute_exam_achievement()의 achievement 가 "정상 경로 시나리오"에서 항상 동일해야 한다.

현재 admin_student_grades_view 는 자체 achievement 계산 로직을 가지고 있고,
compute_exam_achievement 는 유틸 SSOT 이다. 두 로직이 정책상 동등해야 드리프트가
재발하지 않는다. 이 테스트가 실패하면 둘 중 하나의 정책이 바뀐 것이므로 즉시 검토.

검증 시나리오 (정상 운영에서 생성되는 상태만 포함):
  1) PASS            : 1차 점수 ≥ pass_score
  2) REMEDIATED(EXAM_PASS)       : 1차 fail + ClinicLink resolved(EXAM_PASS)
  3) REMEDIATED(MANUAL_OVERRIDE) : 1차 fail + ClinicLink resolved(MANUAL_OVERRIDE)
  4) FAIL            : 1차 fail + 미해소
  5) FAIL(WAIVED)    : 1차 fail + WAIVED (면제는 성취 아님 — 양쪽 모두 FAIL/WAIVED 처리)
  6) NOT_SUBMITTED   : attempt.meta.status=NOT_SUBMITTED
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.domains.clinic.tests import ClinicTestMixin
from apps.domains.exams.models import Exam
from apps.domains.progress.models import ClinicLink
from apps.domains.results.models import ExamAttempt, Result
from apps.domains.results.utils.exam_achievement import compute_exam_achievement
from apps.domains.results.utils.session_exam import get_primary_session_for_exam
from apps.domains.results.views.admin_student_grades_view import AdminStudentGradesView


User = get_user_model()


class AchievementContractTest(TestCase, ClinicTestMixin):
    """admin_student_grades_view vs compute_exam_achievement 정책 동등성."""

    def setUp(self):
        self.data = self.setup_full_tenant("contract", student_count=1)
        self.tenant = self.data["tenant"]
        self.student = self.data["students"][0]
        self.enrollment = self.data["enrollments"][0]
        self.lec_session = self.data["lec_session"]

        # is_effective_staff 통과 조건: is_superuser/staff + tenant_id 일치
        self.admin_user = User.objects.create_user(
            username="contract_admin", password="x",
            is_staff=True, is_superuser=True,
        )
        # User 모델에 tenant_id 필드가 있을 경우만 세팅 (프로젝트 규약)
        if hasattr(self.admin_user, "tenant_id"):
            self.admin_user.tenant_id = self.tenant.id
            self.admin_user.save(update_fields=["tenant_id"])

    # ──────────────────────────────
    # Helpers
    # ──────────────────────────────
    def _make_exam(self, title: str, *, pass_score=60.0) -> Exam:
        exam = Exam.objects.create(
            tenant=self.tenant, title=title,
            pass_score=pass_score, max_score=100.0,
        )
        exam.sessions.add(self.lec_session)
        return exam

    def _make_attempt_and_result(
        self, exam: Exam, *, score: float, meta_status: str | None = None,
    ) -> tuple[ExamAttempt, Result]:
        meta = {"status": meta_status} if meta_status else None
        attempt = ExamAttempt.objects.create(
            exam=exam, enrollment=self.enrollment,
            attempt_index=1, is_representative=True, status="done",
            submission_id=0, meta=meta,
        )
        result = Result.objects.create(
            target_type="exam", target_id=exam.id,
            enrollment=self.enrollment, total_score=score, max_score=100,
            attempt=attempt,
        )
        return attempt, result

    def _resolve_link(self, exam: Exam, *, resolution_type: str) -> ClinicLink:
        link = ClinicLink.objects.create(
            tenant=self.tenant, enrollment=self.enrollment, session=self.lec_session,
            reason="AUTO_FAILED", source_type="exam", source_id=exam.id,
            meta={"kind": "EXAM_FAILED", "exam_id": exam.id},
        )
        link.resolved_at = link.created_at  # any non-null
        link.resolution_type = resolution_type
        link.resolution_evidence = {"exam_id": exam.id, "score": None, "pass_score": None}
        link.save(update_fields=["resolved_at", "resolution_type", "resolution_evidence"])
        return link

    def _view_achievement_for(self, exam_id: int) -> str | None:
        """admin_student_grades_view 응답에서 이 exam 의 achievement 값을 추출."""
        factory = APIRequestFactory()
        request = factory.get(
            "/results/admin/student-grades/",
            {"student_id": self.student.id},
        )
        request.tenant = self.tenant
        force_authenticate(request, user=self.admin_user)

        view = AdminStudentGradesView.as_view()
        response = view(request)
        self.assertEqual(response.status_code, 200, response.data)
        for row in response.data.get("exams", []):
            if int(row["exam_id"]) == int(exam_id):
                return row.get("achievement")
        self.fail(f"exam_id={exam_id} not in response: {response.data}")

    def _util_achievement_for(self, exam: Exam, result: Result) -> str | None:
        session = get_primary_session_for_exam(exam.id)
        data = compute_exam_achievement(
            enrollment_id=self.enrollment.id,
            exam_id=exam.id,
            session=session,
            total_score=float(result.total_score or 0.0),
            pass_score=float(exam.pass_score or 0.0),
            attempt_id=result.attempt_id,
        )
        return data["achievement"]

    def _assert_consistent(self, exam: Exam, result: Result, *, expected: str | None):
        view_val = self._view_achievement_for(exam.id)
        util_val = self._util_achievement_for(exam, result)
        self.assertEqual(
            view_val, util_val,
            f"drift: view={view_val!r} util={util_val!r} exam={exam.title}",
        )
        self.assertEqual(view_val, expected, f"view value mismatch: {view_val!r}")

    # ──────────────────────────────
    # Scenarios
    # ──────────────────────────────
    def test_pass(self):
        exam = self._make_exam("pass", pass_score=60.0)
        _, result = self._make_attempt_and_result(exam, score=80)
        self._assert_consistent(exam, result, expected="PASS")

    def test_remediated_via_exam_pass(self):
        exam = self._make_exam("rem_exam", pass_score=60.0)
        _, result = self._make_attempt_and_result(exam, score=40)
        self._resolve_link(exam, resolution_type=ClinicLink.ResolutionType.EXAM_PASS)
        self._assert_consistent(exam, result, expected="REMEDIATED")

    def test_remediated_via_manual_override(self):
        exam = self._make_exam("rem_manual", pass_score=60.0)
        _, result = self._make_attempt_and_result(exam, score=40)
        self._resolve_link(exam, resolution_type=ClinicLink.ResolutionType.MANUAL_OVERRIDE)
        self._assert_consistent(exam, result, expected="REMEDIATED")

    def test_fail_unresolved(self):
        exam = self._make_exam("fail", pass_score=60.0)
        _, result = self._make_attempt_and_result(exam, score=30)
        self._assert_consistent(exam, result, expected="FAIL")

    def test_waived_is_fail_on_both_sides(self):
        exam = self._make_exam("waived", pass_score=60.0)
        _, result = self._make_attempt_and_result(exam, score=30)
        self._resolve_link(exam, resolution_type=ClinicLink.ResolutionType.WAIVED)
        # WAIVED 는 admin_student_grades_view 의 resolution_type__in 에도 없고
        # compute_exam_achievement 의 remediated 리스트에도 없음 → 양쪽 모두 FAIL.
        self._assert_consistent(exam, result, expected="FAIL")

    def test_not_submitted(self):
        exam = self._make_exam("not_sub", pass_score=60.0)
        _, result = self._make_attempt_and_result(
            exam, score=0, meta_status="NOT_SUBMITTED",
        )
        self._assert_consistent(exam, result, expected="NOT_SUBMITTED")
