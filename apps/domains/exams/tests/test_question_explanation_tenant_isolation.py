"""
QuestionExplanationDetailView 테넌트 격리 테스트.

검증 대상: GET/PUT /exams/questions/<question_id>/explanation/
- 자기 테넌트 문항 해설 조회/수정 → 200
- 다른 테넌트 문항 해설 조회/수정 → 404 (테넌트 필터 차단)
"""
from django.test import TestCase
from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant
from apps.core.models.tenant_membership import TenantMembership
from apps.domains.lectures.models import Lecture, Session
from apps.domains.exams.models import Exam, ExamQuestion
from apps.domains.exams.models.sheet import Sheet
from apps.domains.exams.views.question_explanation_view import QuestionExplanationDetailView

User = get_user_model()


class QuestionExplanationTenantIsolationTest(TestCase):
    """QuestionExplanationDetailView 크로스 테넌트 차단 검증."""

    @classmethod
    def setUpTestData(cls):
        # ── Tenant A ──
        cls.tenant_a = Tenant.objects.create(name="Academy A", code="tenant-a")
        cls.user_a = User.objects.create_user(
            username="t_a_user", password="pass", tenant=cls.tenant_a,
            is_staff=True,
        )
        TenantMembership.objects.create(
            tenant=cls.tenant_a, user=cls.user_a, role="teacher", is_active=True,
        )
        cls.lecture_a = Lecture.objects.create(
            tenant=cls.tenant_a, title="Lecture A", name="Lecture A", subject="math",
        )
        cls.session_a = Session.objects.create(
            lecture=cls.lecture_a, title="Session A", order=1,
        )
        cls.exam_a = Exam.objects.create(
            tenant=cls.tenant_a, title="Exam A",
            exam_type=Exam.ExamType.TEMPLATE, subject="math",
        )
        cls.exam_a.sessions.add(cls.session_a)
        cls.sheet_a = Sheet.objects.create(exam=cls.exam_a)
        cls.question_a = ExamQuestion.objects.create(
            sheet=cls.sheet_a, number=1, score=10,
        )

        # ── Tenant B ──
        cls.tenant_b = Tenant.objects.create(name="Academy B", code="tenant-b")
        cls.user_b = User.objects.create_user(
            username="t_b_user", password="pass", tenant=cls.tenant_b,
            is_staff=True,
        )
        TenantMembership.objects.create(
            tenant=cls.tenant_b, user=cls.user_b, role="teacher", is_active=True,
        )
        cls.lecture_b = Lecture.objects.create(
            tenant=cls.tenant_b, title="Lecture B", name="Lecture B", subject="english",
        )
        cls.session_b = Session.objects.create(
            lecture=cls.lecture_b, title="Session B", order=1,
        )
        cls.exam_b = Exam.objects.create(
            tenant=cls.tenant_b, title="Exam B",
            exam_type=Exam.ExamType.TEMPLATE, subject="english",
        )
        cls.exam_b.sessions.add(cls.session_b)
        cls.sheet_b = Sheet.objects.create(exam=cls.exam_b)
        cls.question_b = ExamQuestion.objects.create(
            sheet=cls.sheet_b, number=1, score=10,
        )

    def _make_request(self, method, user, tenant, question_id, data=None):
        factory = APIRequestFactory()
        if method == "GET":
            request = factory.get(f"/exams/questions/{question_id}/explanation/")
        else:
            request = factory.put(
                f"/exams/questions/{question_id}/explanation/",
                data=data or {},
                format="json",
            )
        force_authenticate(request, user=user)
        request.tenant = tenant
        view = QuestionExplanationDetailView.as_view()
        return view(request, question_id=question_id)

    # ── 정상 케이스: 자기 테넌트 문항 접근 ──

    def test_get_own_tenant_question_returns_200(self):
        """자기 테넌트 문항 해설 조회 → 200."""
        resp = self._make_request("GET", self.user_a, self.tenant_a, self.question_a.id)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_put_own_tenant_question_returns_200(self):
        """자기 테넌트 문항 해설 수정 → 200."""
        resp = self._make_request(
            "PUT", self.user_a, self.tenant_a, self.question_a.id,
            data={"text": "해설 내용", "image_key": ""},
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    # ── 크로스 테넌트 차단 ──

    def test_get_other_tenant_question_returns_404(self):
        """다른 테넌트 문항 해설 조회 → 404 (테넌트 필터 차단)."""
        resp = self._make_request("GET", self.user_a, self.tenant_a, self.question_b.id)
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_put_other_tenant_question_returns_404(self):
        """다른 테넌트 문항 해설 수정 → 404 (테넌트 필터 차단)."""
        resp = self._make_request(
            "PUT", self.user_a, self.tenant_a, self.question_b.id,
            data={"text": "악의적 수정", "image_key": ""},
        )
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_tenant_b_cannot_access_tenant_a_question(self):
        """역방향도 차단: Tenant B → Tenant A 문항 접근 → 404."""
        resp = self._make_request("GET", self.user_b, self.tenant_b, self.question_a.id)
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)
