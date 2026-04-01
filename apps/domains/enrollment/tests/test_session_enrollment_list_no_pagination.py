"""
회귀 테스트: SessionEnrollment list API가 페이지네이션 없이 전체 결과를 반환하는지 검증.

배경:
- PAGE_SIZE=20 글로벌 기본값 때문에 20명 초과 시 첫 페이지만 반환되던 버그 (2026-03-28 수정)
- "직전차시에서 불러오기" 기능에서 일부만 복사되는 증상의 근본 원인
- SessionEnrollmentViewSet.pagination_class = None 으로 수정

경계값 시나리오: 0 / 1 / 19 / 20 / 21 / 40
"""

from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from apps.core.models import Tenant, TenantMembership
from apps.domains.lectures.models import Lecture, Session
from apps.domains.enrollment.models import Enrollment, SessionEnrollment
from apps.domains.students.models import Student


class SessionEnrollmentListNoPaginationTests(APITestCase):
    """SessionEnrollment list API가 pagination 없이 전체 반환하는지 검증."""

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Test", code="9998", is_active=True)

        User = get_user_model()
        self.user = User.objects.create(
            tenant=self.tenant,
            username=f"t{self.tenant.id}_admin",
            is_active=True,
            is_staff=True,
        )
        self.user.set_password("pass1234!")
        self.user.save(update_fields=["password"])

        TenantMembership.objects.create(
            user=self.user, tenant=self.tenant, role="admin", is_active=True,
        )

        self.lecture = Lecture.objects.create(
            tenant=self.tenant, title="Lec", name="Lec", subject="MATH",
        )
        self.session = Session.objects.create(
            lecture=self.lecture, order=1, title="S1",
        )

        self.client.force_authenticate(user=self.user)

    def _headers(self):
        return {"HTTP_HOST": "localhost", "HTTP_X_TENANT_CODE": self.tenant.code}

    def _create_session_enrollments(self, count: int):
        """주어진 수만큼 학생 → Enrollment → SessionEnrollment 생성."""
        User = get_user_model()
        for i in range(count):
            ps_number = f"T{i:05d}"
            student_user = User.objects.create(
                tenant=self.tenant,
                username=f"t{self.tenant.id}_{ps_number}",
                is_active=True,
            )
            student = Student.objects.create(
                tenant=self.tenant,
                user=student_user,
                name=f"Student_{i:03d}",
                ps_number=ps_number,
            )
            enrollment = Enrollment.objects.create(
                tenant=self.tenant,
                student=student,
                lecture=self.lecture,
                status="ACTIVE",
            )
            SessionEnrollment.objects.create(
                tenant=self.tenant,
                session=self.session,
                enrollment=enrollment,
            )

    def _get_list(self):
        """GET /api/v1/enrollments/session-enrollments/?session={id}"""
        resp = self.client.get(
            "/api/v1/enrollments/session-enrollments/",
            {"session": self.session.id},
            **self._headers(),
        )
        return resp

    # ── 경계값 시나리오 ──────────────────────────────────────────

    def test_0_returns_empty_list(self):
        resp = self._get_list()
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIsInstance(data, list)
        self.assertEqual(len(data), 0)

    def test_1_returns_all(self):
        self._create_session_enrollments(1)
        resp = self._get_list()
        data = resp.json()
        self.assertIsInstance(data, list)
        self.assertEqual(len(data), 1)

    def test_19_returns_all(self):
        self._create_session_enrollments(19)
        resp = self._get_list()
        data = resp.json()
        self.assertIsInstance(data, list)
        self.assertEqual(len(data), 19)

    def test_20_returns_all_not_paginated(self):
        """PAGE_SIZE=20 경계: 정확히 20건도 잘라지지 않아야 한다."""
        self._create_session_enrollments(20)
        resp = self._get_list()
        data = resp.json()
        self.assertIsInstance(data, list)
        self.assertEqual(len(data), 20)
        # pagination wrapper가 아닌 flat list 확인
        self.assertNotIn("count", resp.json() if isinstance(resp.json(), dict) else {})

    def test_21_returns_all_exceeding_default_page_size(self):
        """PAGE_SIZE=20 초과: 21건도 전부 반환되어야 한다 (핵심 회귀 케이스)."""
        self._create_session_enrollments(21)
        resp = self._get_list()
        data = resp.json()
        self.assertIsInstance(data, list)
        self.assertEqual(len(data), 21)

    def test_40_returns_all(self):
        """PAGE_SIZE의 2배: 전부 반환 확인."""
        self._create_session_enrollments(40)
        resp = self._get_list()
        data = resp.json()
        self.assertIsInstance(data, list)
        self.assertEqual(len(data), 40)

    def test_response_is_flat_list_not_paginated_object(self):
        """응답이 {count, results} 형태가 아닌 flat list여야 한다."""
        self._create_session_enrollments(5)
        resp = self._get_list()
        data = resp.json()
        # pagination_class=None → flat list 반환
        self.assertIsInstance(data, list)
        # dict(paginated)가 아님을 확인
        self.assertNotIsInstance(data, dict)

    def test_each_row_has_student_id_and_student_name(self):
        """직전차시 불러오기에 필요한 student_id, student_name 필드 존재 확인."""
        self._create_session_enrollments(3)
        resp = self._get_list()
        data = resp.json()
        for row in data:
            self.assertIn("student_id", row)
            self.assertIn("student_name", row)
            self.assertIsInstance(row["student_id"], int)
            self.assertIsInstance(row["student_name"], str)
