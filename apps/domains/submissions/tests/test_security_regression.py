# PATH: apps/domains/submissions/tests/test_security_regression.py
"""
보안 회귀 — 2026-04-25 정밀검사에서 차단한 학생/학부모 권한 누출 경로.

검증 대상:
  C-1  SubmissionViewSet — 학생이 list/retrieve/retry/manual_edit/discard 불가
  C-1b SubmissionViewSet — 학생 본인 enrollment의 create는 정상 통과
  C-2  PendingSubmissionsView — 학생 차단, 교사·어드민 통과
  C-2  ExamSubmissionsListView — 학생 차단
  C-2  HomeworkSubmissionsListView — 학생 차단
  C-3  ExamOMRSubmitView — 학생 차단 (운영자 대리 업로드 경로)

권한 게이트 단계 (HTTP) 로 검증한다. 200/403/401 만 본다 — 비즈니스 로직은
별도 테스트가 커버.
"""
from __future__ import annotations

from unittest.mock import patch

from django.test import TestCase
from django.contrib.auth import get_user_model
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models.tenant import Tenant
from apps.core.models.tenant_membership import TenantMembership
from apps.core.models.user import user_internal_username
from apps.domains.students.models import Student
from apps.domains.lectures.models import Lecture, Session as LectureSession
from apps.domains.enrollment.models import Enrollment
from apps.domains.exams.models import Exam
from apps.domains.submissions.models import Submission
from apps.domains.submissions.views.submission_view import SubmissionViewSet
from apps.domains.submissions.views.pending_submissions_view import PendingSubmissionsView
from apps.domains.submissions.views.exam_submissions_list_view import ExamSubmissionsListView
from apps.domains.submissions.views.homework_submissions_list_view import HomeworkSubmissionsListView
from apps.domains.submissions.views.exam_omr_submit_view import ExamOMRSubmitView
from apps.domains.results.views.admin_landing_stats_view import AdminResultsLandingStatsView
from apps.support.video.views.admin_landing_stats_view import AdminVideosLandingStatsView

User = get_user_model()


def _make_tenant(name, code):
    return Tenant.objects.create(name=name, code=code, is_active=True)


def _make_admin(tenant, username, role="owner"):
    u = User.objects.create_user(
        username=username, password="test1234",
        tenant=tenant, is_staff=True, name=f"Admin-{username}",
    )
    TenantMembership.ensure_active(tenant=tenant, user=u, role=role)
    return u


def _make_teacher(tenant, username):
    u = User.objects.create_user(
        username=username, password="test1234",
        tenant=tenant, name=f"Teacher-{username}",
    )
    TenantMembership.ensure_active(tenant=tenant, user=u, role="teacher")
    return u


def _make_student_user_and_profile(tenant, ps_number, name="학생"):
    internal = user_internal_username(tenant, ps_number)
    user = User.objects.create_user(
        username=internal, password="test1234",
        tenant=tenant, phone=f"010-1111-{ps_number[-4:]:>04}",
        name=name,
    )
    student = Student.objects.create(
        tenant=tenant, user=user,
        ps_number=ps_number, name=name,
        phone=f"010-1111-{ps_number[-4:]:>04}",
        parent_phone=f"010-2222-{ps_number[-4:]:>04}",
        omr_code=ps_number[:8].rjust(8, "0"),
    )
    TenantMembership.ensure_active(tenant=tenant, user=user, role="student")
    return user, student


class _SecurityFixtureMixin:
    """공통 fixture — 1 tenant + 학생 1 + 교사 1 + 어드민 1."""

    def _setup_fixtures(self):
        self.factory = APIRequestFactory()
        self.tenant = _make_tenant("SecAcademy", "sec_test")

        self.admin = _make_admin(self.tenant, "sec_admin")
        self.teacher = _make_teacher(self.tenant, "sec_teacher")
        self.student_user, self.student = _make_student_user_and_profile(
            self.tenant, "S001"
        )

        # lecture/session/exam — submission_view.create 검증용
        self.lecture = Lecture.objects.create(
            tenant=self.tenant, title="Math", name="Math", subject="MATH",
        )
        self.session = LectureSession.objects.create(
            lecture=self.lecture, order=1, title="S1",
        )
        self.enrollment = Enrollment.objects.create(
            tenant=self.tenant, student=self.student,
            lecture=self.lecture, status="ACTIVE",
        )
        self.exam = Exam.objects.create(
            tenant=self.tenant,
            title="Test Exam",
            exam_type=Exam.ExamType.REGULAR,
            pass_score=60, max_score=100, max_attempts=1,
        )
        self.exam.sessions.add(self.session)

        # 학생이 자기 것이 아닌 다른 학생의 submission — list/retrieve 누출 확인용
        self.peer_user, self.peer_student = _make_student_user_and_profile(
            self.tenant, "S002", name="피어"
        )
        self.peer_enrollment = Enrollment.objects.create(
            tenant=self.tenant, student=self.peer_student,
            lecture=self.lecture, status="ACTIVE",
        )
        self.peer_submission = Submission.objects.create(
            tenant=self.tenant, user=self.peer_user,
            enrollment_id=self.peer_enrollment.id,
            target_type=Submission.TargetType.EXAM,
            target_id=self.exam.id,
            source=Submission.Source.OMR_SCAN,
            file_key="tenants/x/peer.png",
        )

    def _call(self, view_factory, method, path, user, *,
              tenant=None, data=None, **view_kwargs):
        m = method.lower()
        if m == "get":
            req = self.factory.get(path)
        elif m == "post":
            req = self.factory.post(path, data=data, format="json")
        else:
            raise ValueError(f"unsupported method {m}")
        force_authenticate(req, user=user)
        req.tenant = tenant or self.tenant
        return view_factory()(req, **view_kwargs)


# ═══════════════════════════════════════════════════
# C-1 SubmissionViewSet — list / retrieve / actions
# ═══════════════════════════════════════════════════

class TestC1SubmissionViewSetGuard(_SecurityFixtureMixin, TestCase):

    def setUp(self):
        self._setup_fixtures()

    # --- list ---

    def test_student_list_blocked(self):
        """학생 → SubmissionViewSet.list → 403 (Staff 전용)."""
        view = SubmissionViewSet.as_view({"get": "list"})
        resp = self._call(lambda: view, "get", "/api/v1/submissions/submissions/",
                          user=self.student_user)
        self.assertEqual(resp.status_code, 403,
                         f"CRITICAL: 학생이 list에 접근 가능 (peer.id={self.peer_submission.id})")

    def test_teacher_list_allowed(self):
        """교사 → SubmissionViewSet.list → 200."""
        view = SubmissionViewSet.as_view({"get": "list"})
        resp = self._call(lambda: view, "get", "/api/v1/submissions/submissions/",
                          user=self.teacher)
        self.assertEqual(resp.status_code, 200)

    def test_admin_list_allowed(self):
        """어드민 → SubmissionViewSet.list → 200."""
        view = SubmissionViewSet.as_view({"get": "list"})
        resp = self._call(lambda: view, "get", "/api/v1/submissions/submissions/",
                          user=self.admin)
        self.assertEqual(resp.status_code, 200)

    # --- retrieve ---

    def test_student_retrieve_peer_blocked(self):
        """학생 → SubmissionViewSet.retrieve(타인) → 403."""
        view = SubmissionViewSet.as_view({"get": "retrieve"})
        resp = self._call(lambda: view, "get",
                          f"/api/v1/submissions/submissions/{self.peer_submission.id}/",
                          user=self.student_user, pk=self.peer_submission.id)
        self.assertEqual(resp.status_code, 403)

    # --- actions ---

    def test_student_retry_blocked(self):
        """학생 → retry → 403 (Staff 전용)."""
        view = SubmissionViewSet.as_view({"post": "retry"})
        resp = self._call(lambda: view, "post",
                          f"/api/v1/submissions/submissions/{self.peer_submission.id}/retry/",
                          user=self.student_user, data={}, pk=self.peer_submission.id)
        self.assertEqual(resp.status_code, 403)

    def test_student_manual_edit_blocked(self):
        """학생 → manual_edit (타인 점수 덮어쓰기 시도) → 403."""
        view = SubmissionViewSet.as_view({"post": "manual_edit"})
        resp = self._call(lambda: view, "post",
                          f"/api/v1/submissions/submissions/{self.peer_submission.id}/manual-edit/",
                          user=self.student_user,
                          data={"identifier": {"enrollment_id": self.enrollment.id},
                                "answers": []},
                          pk=self.peer_submission.id)
        self.assertEqual(resp.status_code, 403,
                         "CRITICAL: 학생이 타인 답안 manual_edit 가능 — "
                         "다른 학생 성적 덮어쓰기 경로!")

    def test_student_discard_blocked(self):
        """학생 → discard → 403."""
        view = SubmissionViewSet.as_view({"post": "discard"})
        resp = self._call(lambda: view, "post",
                          f"/api/v1/submissions/submissions/{self.peer_submission.id}/discard/",
                          user=self.student_user, data={"reason": "abuse"},
                          pk=self.peer_submission.id)
        self.assertEqual(resp.status_code, 403)


# ═══════════════════════════════════════════════════
# C-1b SubmissionViewSet.create — 학생 본인 enrollment는 통과
# ═══════════════════════════════════════════════════

class TestC1bStudentCreateAllowed(_SecurityFixtureMixin, TestCase):
    """학생이 본인 enrollment로 POST 하는 경로(=학생 앱 제출)는 닫히지 않아야 한다."""

    def setUp(self):
        self._setup_fixtures()

    def test_student_create_own_enrollment_passes_permission(self):
        """학생 → POST /submissions/submissions/ (본인 enrollment) → 권한 단계 통과.

        permission 단계만 검증 — dispatch_submission(채점 호출)은 격리해
        권한 결과만 본다.
        """
        view = SubmissionViewSet.as_view({"post": "create"})
        with patch("apps.domains.submissions.views.submission_view.dispatch_submission"):
            resp = self._call(lambda: view, "post",
                              "/api/v1/submissions/submissions/",
                              user=self.student_user,
                              data={
                                  "target_type": "exam",
                                  "target_id": self.exam.id,
                                  "source": "online",
                                  "enrollment_id": self.enrollment.id,
                                  "payload": {"answers": []},
                              })
        # permission 통과 = 401/403 이 아님.
        self.assertNotIn(resp.status_code, (401, 403),
                         f"학생 본인 enrollment 제출이 권한에서 막힘: "
                         f"{resp.status_code} {getattr(resp, 'data', '')}")

    def test_student_create_peer_enrollment_blocked(self):
        """학생 → POST /submissions/submissions/ (타인 enrollment) → 403 (perform_create 소유권 검증)."""
        view = SubmissionViewSet.as_view({"post": "create"})
        with patch("apps.domains.submissions.views.submission_view.dispatch_submission"):
            resp = self._call(lambda: view, "post",
                              "/api/v1/submissions/submissions/",
                              user=self.student_user,
                              data={
                                  "target_type": "exam",
                                  "target_id": self.exam.id,
                                  "source": "online",
                                  "enrollment_id": self.peer_enrollment.id,
                                  "payload": {"answers": []},
                              })
        self.assertEqual(resp.status_code, 403,
                         "CRITICAL: 학생이 타인 enrollment_id로 제출 가능!")


# ═══════════════════════════════════════════════════
# C-2 어드민 인박스 3종 — 학생 차단 / 스태프 통과
# ═══════════════════════════════════════════════════

class TestC2AdminInboxesGuard(_SecurityFixtureMixin, TestCase):

    def setUp(self):
        self._setup_fixtures()

    def test_pending_submissions_student_blocked(self):
        view = PendingSubmissionsView.as_view()
        resp = self._call(lambda: view, "get",
                          "/api/v1/submissions/submissions/pending/",
                          user=self.student_user)
        self.assertEqual(resp.status_code, 403)

    def test_pending_submissions_teacher_allowed(self):
        view = PendingSubmissionsView.as_view()
        resp = self._call(lambda: view, "get",
                          "/api/v1/submissions/submissions/pending/",
                          user=self.teacher)
        self.assertEqual(resp.status_code, 200)

    def test_exam_submissions_list_student_blocked(self):
        view = ExamSubmissionsListView.as_view()
        resp = self._call(lambda: view, "get",
                          f"/api/v1/submissions/submissions/exams/{self.exam.id}/",
                          user=self.student_user, exam_id=self.exam.id)
        self.assertEqual(resp.status_code, 403)

    def test_homework_submissions_list_student_blocked(self):
        view = HomeworkSubmissionsListView.as_view()
        resp = self._call(lambda: view, "get",
                          "/api/v1/submissions/submissions/homework/9999/",
                          user=self.student_user, homework_id=9999)
        self.assertEqual(resp.status_code, 403)


# ═══════════════════════════════════════════════════
# C-3 ExamOMRSubmitView — 학생 차단
# ═══════════════════════════════════════════════════

class TestC3ExamOMRSubmitGuard(_SecurityFixtureMixin, TestCase):

    def setUp(self):
        self._setup_fixtures()

    def test_student_omr_submit_peer_enrollment_blocked(self):
        """학생이 타 학생 enrollment_id로 OMR 제출 시도 → 403 (Staff 전용으로 상향됨)."""
        view = ExamOMRSubmitView.as_view()
        resp = self._call(lambda: view, "post",
                          f"/api/v1/submissions/submissions/exams/{self.exam.id}/omr/",
                          user=self.student_user,
                          data={"enrollment_id": self.peer_enrollment.id,
                                "sheet_id": 1,
                                "file_key": "tenants/x/fake.png"},
                          exam_id=self.exam.id)
        self.assertEqual(resp.status_code, 403,
                         "CRITICAL: 학생이 타 학생 enrollment로 OMR 제출 가능!")

    def test_student_omr_submit_own_enrollment_still_blocked(self):
        """OMR 제출은 운영자 전용 경로 — 자기 enrollment여도 학생은 차단."""
        view = ExamOMRSubmitView.as_view()
        resp = self._call(lambda: view, "post",
                          f"/api/v1/submissions/submissions/exams/{self.exam.id}/omr/",
                          user=self.student_user,
                          data={"enrollment_id": self.enrollment.id,
                                "sheet_id": 1,
                                "file_key": "tenants/x/own.png"},
                          exam_id=self.exam.id)
        self.assertEqual(resp.status_code, 403)

    def test_teacher_omr_submit_passes_permission(self):
        """교사 → 권한 통과 (실제 처리는 비즈니스 단계 — 200/201/400 모두 OK)."""
        view = ExamOMRSubmitView.as_view()
        resp = self._call(lambda: view, "post",
                          f"/api/v1/submissions/submissions/exams/{self.exam.id}/omr/",
                          user=self.teacher,
                          data={"enrollment_id": self.enrollment.id,
                                "sheet_id": 1,
                                "file_key": "tenants/x/teacher_upload.png"},
                          exam_id=self.exam.id)
        self.assertNotIn(resp.status_code, (401, 403))


# ═══════════════════════════════════════════════════
# C-5 Landing Stats — 학생 차단 / 스태프 통과 / 크로스테넌트 차단
# (2026-04-26 KPI 인박스 재설계 회귀 가드)
# ═══════════════════════════════════════════════════

class TestC5LandingStatsGuard(_SecurityFixtureMixin, TestCase):

    def setUp(self):
        self._setup_fixtures()
        # cross-tenant 검증용 다른 테넌트 + 어드민
        self.other_tenant = _make_tenant("OtherAcademy", "sec_other")
        self.other_admin = _make_admin(self.other_tenant, "other_admin")

    # --- Results landing-stats ---

    def test_results_landing_stats_student_blocked(self):
        """학생 → /results/admin/landing-stats/ → 403."""
        view = AdminResultsLandingStatsView.as_view()
        resp = self._call(lambda: view, "get",
                          "/api/v1/results/admin/landing-stats/",
                          user=self.student_user)
        self.assertEqual(resp.status_code, 403)

    def test_results_landing_stats_teacher_allowed(self):
        view = AdminResultsLandingStatsView.as_view()
        resp = self._call(lambda: view, "get",
                          "/api/v1/results/admin/landing-stats/",
                          user=self.teacher)
        self.assertEqual(resp.status_code, 200)

    def test_results_landing_stats_admin_allowed(self):
        view = AdminResultsLandingStatsView.as_view()
        resp = self._call(lambda: view, "get",
                          "/api/v1/results/admin/landing-stats/",
                          user=self.admin)
        self.assertEqual(resp.status_code, 200)

    def test_results_landing_stats_cross_tenant_isolated(self):
        """다른 테넌트 어드민이 self.tenant 헤더로 호출 → 403 (멤버십 없음)."""
        view = AdminResultsLandingStatsView.as_view()
        resp = self._call(lambda: view, "get",
                          "/api/v1/results/admin/landing-stats/",
                          user=self.other_admin, tenant=self.tenant)
        self.assertEqual(resp.status_code, 403,
                         "CRITICAL: 크로스테넌트 어드민이 다른 학원 KPI 조회 가능!")

    def test_results_landing_stats_isolation_payload(self):
        """다른 테넌트의 submission이 self.tenant 응답에 섞이지 않는다."""
        # 다른 테넌트에 lecture/exam/submission 생성
        other_lecture = Lecture.objects.create(
            tenant=self.other_tenant, title="OtherMath", name="OtherMath", subject="MATH",
        )
        other_session = LectureSession.objects.create(
            lecture=other_lecture, order=1, title="OtherS1",
        )
        other_exam = Exam.objects.create(
            tenant=self.other_tenant, title="OtherExam",
            exam_type=Exam.ExamType.REGULAR, pass_score=60, max_score=100, max_attempts=1,
        )
        other_exam.sessions.add(other_session)

        view = AdminResultsLandingStatsView.as_view()
        resp = self._call(lambda: view, "get",
                          "/api/v1/results/admin/landing-stats/",
                          user=self.admin)
        self.assertEqual(resp.status_code, 200)
        body = resp.data
        # self.tenant에는 active_lecture 1, active_exam 1 (테스트 fixture 기준)
        self.assertEqual(body["active_lectures"], 1, body)
        self.assertEqual(body["active_exams"], 1, body)

    # --- Videos landing-stats ---

    def test_videos_landing_stats_student_blocked(self):
        view = AdminVideosLandingStatsView.as_view()
        resp = self._call(lambda: view, "get",
                          "/api/v1/media/admin/videos/landing-stats/",
                          user=self.student_user)
        self.assertEqual(resp.status_code, 403)

    def test_videos_landing_stats_teacher_allowed(self):
        view = AdminVideosLandingStatsView.as_view()
        resp = self._call(lambda: view, "get",
                          "/api/v1/media/admin/videos/landing-stats/",
                          user=self.teacher)
        self.assertEqual(resp.status_code, 200)

    def test_videos_landing_stats_admin_allowed(self):
        view = AdminVideosLandingStatsView.as_view()
        resp = self._call(lambda: view, "get",
                          "/api/v1/media/admin/videos/landing-stats/",
                          user=self.admin)
        self.assertEqual(resp.status_code, 200)

    def test_videos_landing_stats_cross_tenant_blocked(self):
        view = AdminVideosLandingStatsView.as_view()
        resp = self._call(lambda: view, "get",
                          "/api/v1/media/admin/videos/landing-stats/",
                          user=self.other_admin, tenant=self.tenant)
        self.assertEqual(resp.status_code, 403,
                         "CRITICAL: 크로스테넌트 어드민이 다른 학원 영상 KPI 조회 가능!")
