# PATH: apps/domains/attendance/tests/test_bulk_create_tenant_isolation.py
"""
attendance bulk_create 테넌트 검증 증명 테스트

- 다른 테넌트 student_id 전송 → 400 (거부)
- 같은 테넌트 student_id → 201 (정상 생성)
- 다른 테넌트 session_id → 404 (거부)
"""
from django.test import TestCase
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models.tenant import Tenant
from apps.core.models.tenant_membership import TenantMembership
from apps.domains.attendance.serializers import AttendanceSerializer
from apps.domains.enrollment.models import Enrollment
from apps.domains.students.models import Student
from apps.domains.lectures.models import Lecture, Session
from apps.domains.attendance.views import AttendanceViewSet

User = get_user_model()


class TestBulkCreateTenantIsolation(TestCase):

    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant_a = Tenant.objects.create(name="A", code="testa", is_active=True)
        self.tenant_b = Tenant.objects.create(name="B", code="testb", is_active=True)

        # Tenant A admin
        self.admin_a = User.objects.create_user(
            username="t_a_adm", password="test1234",
            tenant=self.tenant_a, is_staff=True, name="AdminA",
        )
        TenantMembership.ensure_active(tenant=self.tenant_a, user=self.admin_a, role="owner")

        # Tenant A student
        self.user_a = User.objects.create_user(
            username="t_a_stu", password="test1234",
            tenant=self.tenant_a, name="StudentA",
        )
        self.student_a = Student.objects.create(
            tenant=self.tenant_a, user=self.user_a,
            ps_number="A001", name="StudentA",
            phone="01011110000", parent_phone="01022220000", omr_code="11110000",
        )

        # Tenant B student
        self.user_b = User.objects.create_user(
            username="t_b_stu", password="test1234",
            tenant=self.tenant_b, name="StudentB",
        )
        self.student_b = Student.objects.create(
            tenant=self.tenant_b, user=self.user_b,
            ps_number="B001", name="StudentB",
            phone="01033330000", parent_phone="01044440000", omr_code="33330000",
        )

        # Tenant A lecture + session
        self.lecture_a = Lecture.objects.create(tenant=self.tenant_a, name="강의A")
        self.session_a = Session.objects.create(lecture=self.lecture_a, order=1)

        # Tenant B lecture + session
        self.lecture_b = Lecture.objects.create(tenant=self.tenant_b, name="강의B")
        self.session_b = Session.objects.create(lecture=self.lecture_b, order=1)

    def _call(self, tenant, user, data):
        request = self.factory.post("/api/v1/attendance/bulk_create/", data=data, format="json")
        force_authenticate(request, user=user)
        request.tenant = tenant
        view = AttendanceViewSet.as_view({"post": "bulk_create"})
        return view(request)

    def test_same_tenant_student_accepted(self):
        """같은 테넌트 학생 → 201 정상 생성."""
        resp = self._call(self.tenant_a, self.admin_a, {
            "session": self.session_a.id,
            "students": [self.student_a.id],
        })
        self.assertEqual(resp.status_code, 201, f"응답: {resp.data}")
        self.assertEqual(len(resp.data), 1)

    def test_other_tenant_student_rejected(self):
        """다른 테넌트 학생 ID → 400 거부."""
        resp = self._call(self.tenant_a, self.admin_a, {
            "session": self.session_a.id,
            "students": [self.student_b.id],  # Tenant B 학생
        })
        self.assertEqual(resp.status_code, 400, f"응답: {resp.data}")
        self.assertIn("속하지 않는", str(resp.data))

    def test_mixed_tenant_students_rejected(self):
        """혼합 (같은 + 다른 테넌트) → 400 거부 (전체 거부)."""
        resp = self._call(self.tenant_a, self.admin_a, {
            "session": self.session_a.id,
            "students": [self.student_a.id, self.student_b.id],
        })
        self.assertEqual(resp.status_code, 400)

    def test_deleted_same_tenant_student_rejected(self):
        """같은 테넌트라도 soft-deleted 학생은 출결 roster 대상이 아니다."""
        self.student_a.deleted_at = timezone.now()
        self.student_a.save(update_fields=["deleted_at", "updated_at"])

        resp = self._call(self.tenant_a, self.admin_a, {
            "session": self.session_a.id,
            "students": [self.student_a.id],
        })

        self.assertEqual(resp.status_code, 400, f"응답: {resp.data}")
        self.assertIn(str(self.student_a.id), str(resp.data))

    def test_other_tenant_session_rejected(self):
        """다른 테넌트 세션 ID → 404."""
        resp = self._call(self.tenant_a, self.admin_a, {
            "session": self.session_b.id,  # Tenant B 세션
            "students": [self.student_a.id],
        })
        self.assertEqual(resp.status_code, 404)

    def test_serializer_fk_querysets_are_tenant_scoped(self):
        active_enrollment = Enrollment.objects.create(
            tenant=self.tenant_a,
            student=self.student_a,
            lecture=self.lecture_a,
            status="ACTIVE",
        )
        deleted_user = User.objects.create_user(
            username="t_a_deleted_stu",
            password="test1234",
            tenant=self.tenant_a,
            name="DeletedStudent",
        )
        deleted_student = Student.objects.create(
            tenant=self.tenant_a,
            user=deleted_user,
            ps_number="A002",
            name="DeletedStudent",
            phone="01055550000",
            parent_phone="01066660000",
            omr_code="55550000",
            deleted_at=timezone.now(),
        )
        deleted_enrollment = Enrollment.objects.create(
            tenant=self.tenant_a,
            student=deleted_student,
            lecture=self.lecture_a,
            status="ACTIVE",
        )
        foreign_enrollment = Enrollment.objects.create(
            tenant=self.tenant_b,
            student=self.student_b,
            lecture=self.lecture_b,
            status="ACTIVE",
        )

        request = self.factory.get("/api/v1/lectures/attendance/")
        request.tenant = self.tenant_a
        serializer = AttendanceSerializer(context={"request": request})

        enrollment_ids = set(serializer.fields["enrollment_id"].queryset.values_list("id", flat=True))
        session_ids = set(serializer.fields["session"].queryset.values_list("id", flat=True))

        self.assertIn(active_enrollment.id, enrollment_ids)
        self.assertNotIn(deleted_enrollment.id, enrollment_ids)
        self.assertNotIn(foreign_enrollment.id, enrollment_ids)
        self.assertIn(self.session_a.id, session_ids)
        self.assertNotIn(self.session_b.id, session_ids)
