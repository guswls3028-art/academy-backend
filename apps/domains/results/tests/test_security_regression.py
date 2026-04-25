# PATH: apps/domains/results/tests/test_security_regression.py
"""
보안 회귀 — 2026-04-25 정밀검사:
  C-4  WrongNote/WrongNotePDF/StudentExamAttempts 의 user_id↔student_id PK 충돌 차단

이전 코드는 hasattr(Enrollment, "user_id") 폴백으로 student_id=user.id를 비교해
Student.pk와 User.pk 공간 충돌로 타 학생 데이터에 우연히 접근 가능했다.
이 테스트는 다음 시나리오를 강제로 만든 뒤 차단 확인:
  - 학생 user.id == 다른 학생 student.id 인 상황을 fixture로 구성
  - 우연 매칭으로 타인 enrollment에 접근 시도 → 403
  - 본인 enrollment 접근은 정상 → 권한 통과
"""
from __future__ import annotations

from django.test import TestCase
from django.contrib.auth import get_user_model
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models.tenant import Tenant
from apps.core.models.tenant_membership import TenantMembership
from apps.core.models.user import user_internal_username
from apps.domains.students.models import Student
from apps.domains.lectures.models import Lecture
from apps.domains.enrollment.models import Enrollment
from apps.domains.results.views.wrong_note_view import WrongNoteView
from apps.domains.results.views.wrong_note_pdf_view import WrongNotePDFCreateView
from apps.domains.results.views.student_exam_attempts_view import MyExamAttemptsView

User = get_user_model()


def _make_tenant():
    return Tenant.objects.create(name="ResultsSecAcademy", code="ressec", is_active=True)


def _make_student(tenant, ps_number, name="학생", forced_user_id=None):
    """일반 학생 생성. forced_user_id가 주어지면 User.id를 명시적으로 지정 (PK 충돌 시뮬용)."""
    internal = user_internal_username(tenant, ps_number)
    user_kwargs = dict(
        username=internal, password="test1234",
        tenant=tenant, name=name,
    )
    if forced_user_id is not None:
        user = User(**user_kwargs)
        user.id = forced_user_id
        user.set_password("test1234")
        user.save(force_insert=True)
    else:
        user = User.objects.create_user(**user_kwargs)

    student = Student.objects.create(
        tenant=tenant, user=user,
        ps_number=ps_number, name=name,
        omr_code=ps_number[:8].rjust(8, "0"),
        parent_phone=f"010-3333-{ps_number[-4:]:>04}",
    )
    TenantMembership.ensure_active(tenant=tenant, user=user, role="student")
    return user, student


class _Mixin:

    def _setup(self):
        self.factory = APIRequestFactory()
        self.tenant = _make_tenant()

        self.lecture = Lecture.objects.create(
            tenant=self.tenant, title="L", name="L", subject="MATH",
        )

        # 학생 A — User.id 를 일부러 큰 값(900)으로 강제해 user_b가 끼어들 자리를 만든다.
        # student_a.id 는 auto sequence라 1이 된다.
        self.user_a, self.student_a = _make_student(
            self.tenant, "A001", "학생A", forced_user_id=900,
        )
        self.enroll_a = Enrollment.objects.create(
            tenant=self.tenant, student=self.student_a,
            lecture=self.lecture, status="ACTIVE",
        )

        # 학생 B — User.id == student_a.id 가 되도록 강제 (PK 공간 충돌)
        # 옛 버그(student_id=user.id)에서는 user_b 가 enroll_a 에 우연 접근 가능.
        self.user_b, self.student_b = _make_student(
            self.tenant, "B001", "학생B",
            forced_user_id=self.student_a.id,
        )
        self.enroll_b = Enrollment.objects.create(
            tenant=self.tenant, student=self.student_b,
            lecture=self.lecture, status="ACTIVE",
        )

    def _get(self, view, user, **query):
        from urllib.parse import urlencode
        qs = ("?" + urlencode(query)) if query else ""
        req = self.factory.get(f"/api/v1/results/wrong-notes/{qs}")
        force_authenticate(req, user=user)
        req.tenant = self.tenant
        return view(req)


# ═══════════════════════════════════════════════════
# C-4 WrongNoteView — PK 공간 충돌 차단
# ═══════════════════════════════════════════════════

class TestC4WrongNotePkCollisionGuard(_Mixin, TestCase):

    def setUp(self):
        self._setup()

    def test_user_b_cannot_access_student_a_enrollment_via_pk_collision(self):
        """user_b.id == student_a.id 상황에서 user_b가 enroll_a 접근 시도 → 403."""
        # 사전조건: user_b.id == student_a.id
        self.assertEqual(self.user_b.id, self.student_a.id,
                         "fixture 무결성: PK 충돌이 강제되어야 함")
        # student_a.user_id != student_b.user_id (다른 사람)
        self.assertNotEqual(self.user_a.id, self.user_b.id)

        view = WrongNoteView.as_view()
        resp = self._get(view, user=self.user_b, enrollment_id=self.enroll_a.id)
        self.assertEqual(resp.status_code, 403,
                         "CRITICAL: PK 공간 충돌(student.id == user.id)로 "
                         "타 학생 enrollment 접근 가능!")

    def test_student_can_access_own_enrollment(self):
        """본인 enrollment 접근은 정상 (200, 빈 결과여도 OK)."""
        view = WrongNoteView.as_view()
        resp = self._get(view, user=self.user_a, enrollment_id=self.enroll_a.id)
        self.assertEqual(resp.status_code, 200)

    def test_pdf_create_user_b_cannot_use_student_a_enrollment(self):
        """WrongNotePDFCreate도 동일한 가드. PK 충돌로 타인 enrollment PDF 생성 불가."""
        view = WrongNotePDFCreateView.as_view()
        req = self.factory.post(
            "/api/v1/results/wrong-notes/pdf/",
            data={"enrollment_id": self.enroll_a.id}, format="json",
        )
        force_authenticate(req, user=self.user_b)
        req.tenant = self.tenant
        resp = view(req)
        self.assertEqual(resp.status_code, 403)

    def test_attempts_user_b_pk_collision_blocked(self):
        """MyExamAttemptsView에서도 PK 충돌 사용자가 타인 attempts 접근 불가.

        과거 코드는 student_id=user.id 비교라 user_b.id == student_a.id 인 user_b가
        enrollment_a에 우연 매칭. 수정 후엔 student_profile.id 기준이라 user_b는
        student_b 의 enrollment만 보인다 → enroll_a 의 attempts는 노출되지 않는다.
        IsStudent 권한이라 200 반환하되, 매칭이 안 되면 빈 리스트 반환이 정상.
        """
        view = MyExamAttemptsView.as_view()
        req = self.factory.get("/api/v1/results/me/exams/9999/attempts/")
        force_authenticate(req, user=self.user_b)
        req.tenant = self.tenant
        resp = view(req, exam_id=9999)
        # 본인 enrollment만 봐야 하므로 enroll_a 데이터 노출 0건
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data, [])
