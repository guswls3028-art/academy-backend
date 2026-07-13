"""Session roster bulk-create lifecycle regression tests.

Inactive or pending enrollment is a billing and access lifecycle state. Merely
adding a student to one session must not reactivate it; an operator must first
reactivate the enrollment through the enrollment workflow.
"""

from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from apps.core.models import Tenant, TenantMembership
from apps.domains.lectures.models import Lecture, Session
from apps.domains.enrollment.models import Enrollment, SessionEnrollment
from apps.domains.fees.models import FeeTemplate, StudentFee
from apps.domains.students.models import Student


class SessionEnrollmentBulkCreateReactivateTests(APITestCase):
    """Roster writes preserve the explicit enrollment lifecycle boundary."""

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

    def test_inactive_enrollment_rejected_on_bulk_create(self):
        enrollment = self._create_student_and_enrollment(0, enrollment_status="INACTIVE")
        self.assertEqual(enrollment.status, "INACTIVE")

        resp = self.client.post(
            "/api/v1/enrollments/session-enrollments/bulk_create/",
            {"session": self.session.id, "enrollments": [enrollment.id]},
            format="json",
            **self._headers(),
        )
        self.assertEqual(resp.status_code, 400, resp.data)

        enrollment.refresh_from_db()
        self.assertEqual(enrollment.status, "INACTIVE")

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

    def test_pending_enrollment_rejected(self):
        enrollment = self._create_student_and_enrollment(2, enrollment_status="PENDING")

        resp = self.client.post(
            "/api/v1/enrollments/session-enrollments/bulk_create/",
            {"session": self.session.id, "enrollments": [enrollment.id]},
            format="json",
            **self._headers(),
        )
        self.assertEqual(resp.status_code, 400, resp.data)

        enrollment.refresh_from_db()
        self.assertEqual(enrollment.status, "PENDING")

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

    def test_mixed_active_and_inactive_is_rejected_atomically(self):
        e_active1 = self._create_student_and_enrollment(10, "ACTIVE")
        e_active2 = self._create_student_and_enrollment(11, "ACTIVE")
        e_inactive = self._create_student_and_enrollment(12, "INACTIVE")

        resp = self.client.post(
            "/api/v1/enrollments/session-enrollments/bulk_create/",
            {"session": self.session.id, "enrollments": [e_active1.id, e_inactive.id, e_active2.id]},
            format="json",
            **self._headers(),
        )
        self.assertEqual(resp.status_code, 400, resp.data)

        e_inactive.refresh_from_db()
        self.assertEqual(e_inactive.status, "INACTIVE")
        e_active1.refresh_from_db()
        self.assertEqual(e_active1.status, "ACTIVE")
        e_active2.refresh_from_db()
        self.assertEqual(e_active2.status, "ACTIVE")
        self.assertFalse(
            SessionEnrollment.objects.filter(
                tenant=self.tenant,
                session=self.session,
            ).exists()
        )

    def test_active_enrollment_creates_session_enrollment_and_attendance(self):
        enrollment = self._create_student_and_enrollment(20, enrollment_status="ACTIVE")

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

    def test_active_enrollment_reactivates_auto_assigned_student_fee(self):
        enrollment = self._create_student_and_enrollment(21, enrollment_status="ACTIVE")
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
