from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models.tenant import Tenant
from apps.core.models.tenant_membership import TenantMembership
from apps.domains.attendance.models import Attendance
from apps.domains.attendance.views import AttendanceViewSet, _apply_attendance_ordering
from apps.domains.enrollment.test_support import create_enrollment_fixture
from apps.domains.lectures.test_support import (
    create_lecture_fixture,
    create_session_fixture,
)
from apps.domains.students.test_support import create_student_fixture


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
        self.lecture = create_lecture_fixture(
            tenant=self.tenant,
            title="정렬 강의",
            name="정렬 강의",
            subject="수학",
        )
        self.session = create_session_fixture(lecture=self.lecture, order=1, title="1차시")

        self._create_attendance("하늘", "ABSENT", "01030000000", "01090000003", 1)
        self._create_attendance("가람", "UNSET", "01010000000", "01090000001", 2)
        self._create_attendance("나래", "PRESENT", "01020000000", "01090000002", 3)

        other_admin = User.objects.create_user(
            username="ordering-other-admin",
            password="test1234",
            tenant=self.other_tenant,
            name="다른 관리자",
        )
        other_student = create_student_fixture(
            tenant=self.other_tenant,
            user=other_admin,
            ps_number="OTHER-1",
            omr_code="99000001",
            name="가가",
            phone="01000000000",
            parent_phone="01099999999",
        )
        other_lecture = create_lecture_fixture(
            tenant=self.other_tenant,
            title="다른 강의",
            name="다른 강의",
            subject="수학",
        )
        other_session = create_session_fixture(lecture=other_lecture, order=1, title="1차시")
        other_enrollment = create_enrollment_fixture(
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
        student = create_student_fixture(
            tenant=self.tenant,
            user=user,
            ps_number=f"ORDER-{suffix}",
            omr_code=f"{88000000 + suffix:08d}",
            name=name,
            phone=phone,
            parent_phone=parent_phone,
        )
        enrollment = create_enrollment_fixture(
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
            ("not_arrived", ["가람", "하늘", "나래"]),
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

    def test_not_arrived_order_covers_all_pages_with_stable_ties_and_filters(self):
        original_unset = Attendance.objects.get(session=self.session, status="UNSET")
        duplicate_name = self._create_attendance("가람", "ABSENT", "01040000000", "01090000004", 4)
        self._create_attendance("가나", "ONLINE", "01050000000", "01090000005", 5)
        self._create_attendance("가다", "LATE", "01060000000", "01090000006", 6)

        query = f"session={self.session.id}&ordering=not_arrived&page_size=3"
        first_page = self._list(f"{query}&page=1")
        second_page = self._list(f"{query}&page=2")
        self.assertEqual(first_page["count"], 6)
        self.assertEqual([row["name"] for row in first_page["results"]], ["가람", "가람", "하늘"])
        self.assertEqual([row["id"] for row in first_page["results"][:2]], [original_unset.id, duplicate_name.id])
        self.assertEqual([row["name"] for row in second_page["results"]], ["가나", "가다", "나래"])

        filtered = self._list(f"{query}&status=ABSENT&search=가람")
        self.assertEqual(filtered["count"], 1)
        self.assertEqual([row["id"] for row in filtered["results"]], [duplicate_name.id])

        Attendance.objects.filter(id=duplicate_name.id).update(status="PRESENT")
        refreshed = self._list(f"{query}&page=1")
        self.assertEqual([row["name"] for row in refreshed["results"]], ["가람", "하늘", "가나"])

    def test_letter_suffixes_stay_with_korean_names_across_pages_and_sort_groups(self):
        # Import order is deliberately different from name order. The suffix is
        # part of the stored name; never strip it or move it into a Latin group.
        for suffix, name in enumerate(
            ("황가온", "김가온B", "나누리B", "김가온", "이하루A", "김가온A", "나누리A", "김가온A"),
            start=10,
        ):
            self._create_attendance(name, "UNSET", f"0108000{suffix:04d}", "01090000000", suffix)
        current = Attendance.objects.filter(tenant=self.tenant, session=self.session)
        current.update(status="UNSET")
        expected = sorted(
            current.select_related("enrollment__student"),
            key=lambda row: (row.enrollment.student.name, row.id),
        )
        expected_ids = [row.id for row in expected]

        for ordering in ("name", "status", "not_arrived", "invalid"):
            with self.subTest(ordering=ordering):
                found = []
                for page in range(1, (len(expected) + 3) // 4 + 1):
                    result = self._list(
                        f"session={self.session.id}&ordering={ordering}&page_size=4&page={page}"
                    )
                    self.assertEqual(result["count"], len(expected))
                    found.extend(row["id"] for row in result["results"])
                self.assertEqual(found, expected_ids)
                ordered = _apply_attendance_ordering(current, ordering)
                if connection.vendor == "postgresql":
                    self.assertIn('COLLATE "C"', str(ordered.query))

        descending = self._list(f"session={self.session.id}&ordering=-name&page_size=50")
        self.assertEqual(
            [row["id"] for row in descending["results"]],
            [row.id for row in sorted(expected, key=lambda row: row.enrollment.student.name, reverse=True)],
        )
        filtered = self._list(f"session={self.session.id}&search=김가온&ordering=name")
        self.assertEqual([row["name"] for row in filtered["results"]], ["김가온", "김가온A", "김가온A", "김가온B"])

        # Telephone/status primary ordering must use the same name collation
        # for ties, including descending primary keys and restored invalid input.
        for ordering in ("-name", "phone", "-phone", "parent_phone", "-parent_phone", "-status", "-not_arrived"):
            with self.subTest(ordering=ordering):
                ordered = _apply_attendance_ordering(current, ordering)
                if connection.vendor == "postgresql":
                    self.assertIn('COLLATE "C"', str(ordered.query))
                self.assertEqual(ordered.count(), len(expected))
        phone_ties = self._list(f"session={self.session.id}&ordering=parent_phone&page_size=50")
        self.assertEqual(
            [row["name"] for row in phone_ties["results"][:8]],
            ["김가온", "김가온A", "김가온A", "김가온B", "나누리A", "나누리B", "이하루A", "황가온"],
        )
