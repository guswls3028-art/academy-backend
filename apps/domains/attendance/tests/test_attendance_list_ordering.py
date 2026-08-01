from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models.tenant import Tenant
from apps.core.models.tenant_membership import TenantMembership
from apps.domains.attendance.models import Attendance
from apps.domains.attendance.views import AttendanceViewSet
from apps.domains.enrollment.models import Enrollment
from apps.domains.lectures.models import Lecture, Session
from apps.domains.students.models import Student


User = get_user_model()


class TestAttendanceListOrdering(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = Tenant.objects.create(name="정렬 학원", code="ordering", is_active=True)
        self.other_tenant = Tenant.objects.create(name="다른 학원", code="ordering-other", is_active=True)
        self.admin = User.objects.create_user(
            username="ordering-admin",
            password="test1234",
            tenant=self.tenant,
            is_staff=True,
            name="정렬 관리자",
        )
        TenantMembership.ensure_active(tenant=self.tenant, user=self.admin, role="owner")
        self.lecture = Lecture.objects.create(
            tenant=self.tenant,
            title="정렬 강의",
            name="정렬 강의",
            subject="수학",
        )
        self.session = Session.objects.create(lecture=self.lecture, order=1, title="1차시")

        self._create_attendance("하늘", "ABSENT", "01030000000", "01090000003", 1)
        self._create_attendance("가람", "UNSET", "01010000000", "01090000001", 2)
        self._create_attendance("나래", "PRESENT", "01020000000", "01090000002", 3)

        other_admin = User.objects.create_user(
            username="ordering-other-admin",
            password="test1234",
            tenant=self.other_tenant,
            name="다른 관리자",
        )
        other_student = Student.objects.create(
            tenant=self.other_tenant,
            user=other_admin,
            ps_number="OTHER-1",
            omr_code="99000001",
            name="가가",
            phone="01000000000",
            parent_phone="01099999999",
        )
        other_lecture = Lecture.objects.create(
            tenant=self.other_tenant,
            title="다른 강의",
            name="다른 강의",
            subject="수학",
        )
        other_session = Session.objects.create(lecture=other_lecture, order=1, title="1차시")
        other_enrollment = Enrollment.objects.create(
            tenant=self.other_tenant,
            student=other_student,
            lecture=other_lecture,
        )
        Attendance.objects.create(
            tenant=self.other_tenant,
            session=other_session,
            enrollment=other_enrollment,
            status="UNSET",
        )

    def _create_attendance(self, name, status, phone, parent_phone, suffix):
        user = User.objects.create_user(
            username=f"ordering-student-{suffix}",
            password="test1234",
            tenant=self.tenant,
            name=name,
        )
        student = Student.objects.create(
            tenant=self.tenant,
            user=user,
            ps_number=f"ORDER-{suffix}",
            omr_code=f"8800000{suffix}",
            name=name,
            phone=phone,
            parent_phone=parent_phone,
        )
        enrollment = Enrollment.objects.create(
            tenant=self.tenant,
            student=student,
            lecture=self.lecture,
        )
        return Attendance.objects.create(
            tenant=self.tenant,
            session=self.session,
            enrollment=enrollment,
            status=status,
        )

    def _list(self, query):
        request = self.factory.get(f"/api/v1/lectures/attendance/?{query}")
        force_authenticate(request, user=self.admin)
        request.tenant = self.tenant
        response = AttendanceViewSet.as_view({"get": "list"})(request)
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def test_default_name_order_is_global_before_pagination_and_tenant_scoped(self):
        first_page = self._list(f"session={self.session.id}&page=1&page_size=2")
        second_page = self._list(f"session={self.session.id}&page=2&page_size=2")

        self.assertEqual(first_page["count"], 3)
        self.assertEqual([row["name"] for row in first_page["results"]], ["가람", "나래"])
        self.assertEqual([row["name"] for row in second_page["results"]], ["하늘"])

    def test_supported_ordering_is_stable_and_invalid_values_fail_safe(self):
        cases = (
            ("-name", ["하늘", "나래", "가람"]),
            ("status", ["가람", "나래", "하늘"]),
            ("-status", ["하늘", "나래", "가람"]),
            ("phone", ["가람", "나래", "하늘"]),
            ("-parent_phone", ["하늘", "나래", "가람"]),
            ("unknown", ["가람", "나래", "하늘"]),
        )
        for ordering, expected_names in cases:
            with self.subTest(ordering=ordering):
                data = self._list(
                    f"session={self.session.id}&page_size=50&ordering={ordering}"
                )
                self.assertEqual([row["name"] for row in data["results"]], expected_names)
