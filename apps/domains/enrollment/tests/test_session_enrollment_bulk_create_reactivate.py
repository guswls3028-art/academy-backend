"""
회귀 테스트: SessionEnrollmentViewSet.bulk_create에서 INACTIVE enrollment 재활성화 검증.

배경:
- SessionDetailPage 경로의 bulk_create가 INACTIVE enrollment을 재활성화하지 않던 버그
- attendance/views.py의 bulk_create는 재활성화 로직이 있었지만
  enrollment/views.py의 bulk_create에는 누락되어 있었음 (2026-03-28 수정)
"""

from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from apps.core.models import Tenant, TenantMembership
from apps.domains.lectures.models import Lecture, Session
from apps.domains.enrollment.models import Enrollment, SessionEnrollment
from apps.domains.fees.models import FeeTemplate, StudentFee
from apps.domains.students.models import Student


class SessionEnrollmentBulkCreateReactivateTests(APITestCase):
    """bulk_create에서 INACTIVE enrollment이 ACTIVE로 복원되는지 검증."""

    def setUp(self):
        self.tenant = Tenant.objects.create(name="Test", code="9997", is_active=True)

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

    def _create_student_and_enrollment(self, idx, enrollment_status="ACTIVE"):
        User = get_user_model()
        ps = f"R{idx:05d}"
        student_user = User.objects.create(
            tenant=self.tenant, username=f"t{self.tenant.id}_{ps}", is_active=True,
        )
        student = Student.objects.create(
            tenant=self.tenant, user=student_user, name=f"Stu_{idx}", ps_number=ps,
        )
        enrollment = Enrollment.objects.create(
            tenant=self.tenant, student=student, lecture=self.lecture,
            status=enrollment_status,
        )
        return enrollment

    def test_inactive_enrollment_reactivated_on_bulk_create(self):
        """INACTIVE enrollment이 bulk_create 시 ACTIVE로 복원되어야 한다."""
        enrollment = self._create_student_and_enrollment(0, enrollment_status="INACTIVE")
        self.assertEqual(enrollment.status, "INACTIVE")

        resp = self.client.post(
            "/api/v1/enrollments/session-enrollments/bulk_create/",
            {"session": self.session.id, "enrollments": [enrollment.id]},
            format="json",
            **self._headers(),
        )
        self.assertEqual(resp.status_code, 201)

        enrollment.refresh_from_db()
        self.assertEqual(enrollment.status, "ACTIVE")

    def test_active_enrollment_stays_active(self):
        """이미 ACTIVE인 enrollment은 그대로 ACTIVE."""
        enrollment = self._create_student_and_enrollment(1, enrollment_status="ACTIVE")

        resp = self.client.post(
            "/api/v1/enrollments/session-enrollments/bulk_create/",
            {"session": self.session.id, "enrollments": [enrollment.id]},
            format="json",
            **self._headers(),
        )
        self.assertEqual(resp.status_code, 201)

        enrollment.refresh_from_db()
        self.assertEqual(enrollment.status, "ACTIVE")

    def test_pending_enrollment_reactivated(self):
        """PENDING enrollment도 bulk_create 시 ACTIVE로 복원."""
        enrollment = self._create_student_and_enrollment(2, enrollment_status="PENDING")

        resp = self.client.post(
            "/api/v1/enrollments/session-enrollments/bulk_create/",
            {"session": self.session.id, "enrollments": [enrollment.id]},
            format="json",
            **self._headers(),
        )
        self.assertEqual(resp.status_code, 201)

        enrollment.refresh_from_db()
        self.assertEqual(enrollment.status, "ACTIVE")

    def test_missing_session_returns_validation_error_not_500(self):
        enrollment = self._create_student_and_enrollment(3)

        resp = self.client.post(
            "/api/v1/enrollments/session-enrollments/bulk_create/",
            {"session": 999999, "enrollments": [enrollment.id]},
            format="json",
            **self._headers(),
        )

        self.assertEqual(resp.status_code, 400)

    def test_missing_enrollment_returns_validation_error_not_500(self):
        resp = self.client.post(
            "/api/v1/enrollments/session-enrollments/bulk_create/",
            {"session": self.session.id, "enrollments": [999999]},
            format="json",
            **self._headers(),
        )

        self.assertEqual(resp.status_code, 400)

    def test_mixed_active_and_inactive(self):
        """ACTIVE 2명 + INACTIVE 1명 혼합 시 INACTIVE만 재활성화."""
        e_active1 = self._create_student_and_enrollment(10, "ACTIVE")
        e_active2 = self._create_student_and_enrollment(11, "ACTIVE")
        e_inactive = self._create_student_and_enrollment(12, "INACTIVE")

        resp = self.client.post(
            "/api/v1/enrollments/session-enrollments/bulk_create/",
            {"session": self.session.id, "enrollments": [e_active1.id, e_inactive.id, e_active2.id]},
            format="json",
            **self._headers(),
        )
        self.assertEqual(resp.status_code, 201)
        data = resp.json()
        self.assertEqual(len(data), 3)

        e_inactive.refresh_from_db()
        self.assertEqual(e_inactive.status, "ACTIVE")
        e_active1.refresh_from_db()
        self.assertEqual(e_active1.status, "ACTIVE")

    def test_reactivated_enrollment_creates_session_enrollment_and_attendance(self):
        """재활성화된 enrollment에 대해 SessionEnrollment + Attendance 모두 생성 확인."""
        enrollment = self._create_student_and_enrollment(20, enrollment_status="INACTIVE")

        resp = self.client.post(
            "/api/v1/enrollments/session-enrollments/bulk_create/",
            {"session": self.session.id, "enrollments": [enrollment.id]},
            format="json",
            **self._headers(),
        )
        self.assertEqual(resp.status_code, 201)

        # SessionEnrollment 존재
        self.assertTrue(
            SessionEnrollment.objects.filter(
                tenant=self.tenant, session=self.session, enrollment=enrollment
            ).exists()
        )

        # Attendance 존재
        from apps.domains.attendance.models import Attendance
        att = Attendance.objects.filter(
            tenant=self.tenant, session=self.session, enrollment=enrollment
        ).first()
        self.assertIsNotNone(att)
        self.assertEqual(att.status, "PRESENT")

    def test_reactivated_enrollment_reactivates_auto_assigned_student_fee(self):
        enrollment = self._create_student_and_enrollment(21, enrollment_status="INACTIVE")
        fee_template = FeeTemplate.objects.create(
            tenant=self.tenant,
            name="월 수강료",
            fee_type=FeeTemplate.FeeType.TUITION,
            billing_cycle=FeeTemplate.BillingCycle.MONTHLY,
            amount=100_000,
            lecture=self.lecture,
            auto_assign=True,
        )
        student_fee = StudentFee.objects.create(
            tenant=self.tenant,
            student=enrollment.student,
            fee_template=fee_template,
            enrollment=enrollment,
            is_active=False,
            billing_end_month="2026-05",
        )

        resp = self.client.post(
            "/api/v1/enrollments/session-enrollments/bulk_create/",
            {"session": self.session.id, "enrollments": [enrollment.id]},
            format="json",
            **self._headers(),
        )

        self.assertEqual(resp.status_code, 201)
        student_fee.refresh_from_db()
        self.assertTrue(student_fee.is_active)
        self.assertEqual(student_fee.billing_end_month, "")
