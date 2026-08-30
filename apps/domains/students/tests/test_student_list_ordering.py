from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from academy.adapters.db.django import repositories_video as video_repo
from apps.core.models.tenant import Tenant
from apps.core.models.tenant_membership import TenantMembership
from apps.domains.enrollment.selectors import (
    enrollments_for_tenant,
    session_enrollments_for_tenant,
)
from apps.domains.enrollment.test_support import (
    create_enrollment_fixture,
    create_session_enrollment_fixture,
)
from apps.domains.lectures.test_support import (
    create_lecture_fixture,
    create_session_fixture,
)
from apps.domains.students.test_support import create_student_fixture
from apps.domains.students.views import StudentViewSet


User = get_user_model()


class TestStudentListOrdering(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = Tenant.objects.create(
            name="학생 정렬 학원",
            code="student-ordering",
            is_active=True,
        )
        self.other_tenant = Tenant.objects.create(
            name="다른 학원",
            code="student-ordering-other",
            is_active=True,
        )
        self.admin = User.objects.create_user(
            username="student-ordering-admin",
            password="test1234",
            tenant=self.tenant,
            is_staff=True,
            name="정렬 관리자",
        )
        TenantMembership.ensure_active(
            tenant=self.tenant,
            user=self.admin,
            role="owner",
        )

        self.haneul = self._create_student("하늘", 1, grade=3)
        self.garam_first = self._create_student("가람", 2, grade=2)
        self.narae = self._create_student("나래", 3, grade=1)
        self.garam_second = self._create_student("가람", 4, grade=1)

        deleted = self._create_student("가나", 5, grade=2)
        deleted.deleted_at = timezone.now()
        deleted.save(update_fields=["deleted_at", "updated_at"])

        other_user = User.objects.create_user(
            username="student-ordering-other-user",
            password="test1234",
            tenant=self.other_tenant,
            name="가가",
        )
        create_student_fixture(
            tenant=self.other_tenant,
            user=other_user,
            ps_number="OTHER-1",
            omr_code="99000001",
            name="가가",
            phone="01099990001",
            parent_phone="01099990002",
        )

    def _create_student(self, name, suffix, *, grade):
        user = User.objects.create_user(
            username=f"student-ordering-{suffix}",
            password="test1234",
            tenant=self.tenant,
            name=name,
        )
        return create_student_fixture(
            tenant=self.tenant,
            user=user,
            ps_number=f"ORDER-{suffix}",
            omr_code=f"8800000{suffix}",
            name=name,
            phone=f"0101000000{suffix}",
            parent_phone=f"0102000000{suffix}",
            grade=grade,
        )

    def _list(self, query=""):
        suffix = f"?{query}" if query else ""
        request = self.factory.get(f"/api/v1/students/{suffix}")
        force_authenticate(request, user=self.admin)
        request.tenant = self.tenant
        response = StudentViewSet.as_view({"get": "list"})(request)
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def test_default_name_order_is_global_before_pagination_and_tenant_scoped(self):
        first_page = self._list("page=1&page_size=2")
        second_page = self._list("page=2&page_size=2")

        self.assertEqual(first_page["count"], 4)
        self.assertEqual(
            [(row["name"], row["id"]) for row in first_page["results"]],
            [("가람", self.garam_first.id), ("가람", self.garam_second.id)],
        )
        self.assertEqual(
            [row["name"] for row in second_page["results"]],
            ["나래", "하늘"],
        )

    def test_requested_ordering_is_stable_and_invalid_values_restore_name_order(self):
        cases = (
            (
                "-name",
                [
                    self.haneul.id,
                    self.narae.id,
                    self.garam_second.id,
                    self.garam_first.id,
                ],
            ),
            (
                "grade",
                [
                    self.garam_second.id,
                    self.narae.id,
                    self.garam_first.id,
                    self.haneul.id,
                ],
            ),
            (
                "unknown",
                [
                    self.garam_first.id,
                    self.garam_second.id,
                    self.narae.id,
                    self.haneul.id,
                ],
            ),
        )
        for ordering, expected_ids in cases:
            with self.subTest(ordering=ordering):
                data = self._list(f"page_size=50&ordering={ordering}")
                self.assertEqual(
                    [row["id"] for row in data["results"]],
                    expected_ids,
                )

    def test_deleted_tab_uses_the_same_default_name_contract(self):
        data = self._list("deleted=true&page_size=50")

        self.assertEqual(data["count"], 1)
        self.assertEqual([row["name"] for row in data["results"]], ["가나"])

    def test_lecture_and_session_enrollment_lists_share_student_name_order(self):
        lecture = create_lecture_fixture(
            tenant=self.tenant,
            title="학생 정렬 강의",
            name="학생 정렬 강의",
            subject="수학",
        )
        session = create_session_fixture(
            lecture=lecture,
            order=1,
            title="1차시",
        )
        enrollments = [
            create_enrollment_fixture(
                tenant=self.tenant,
                student=student,
                lecture=lecture,
            )
            for student in (self.haneul, self.narae, self.garam_first)
        ]
        for enrollment in enrollments:
            create_session_enrollment_fixture(
                tenant=self.tenant,
                session=session,
                enrollment=enrollment,
            )

        self.assertEqual(
            [row.student.name for row in enrollments_for_tenant(self.tenant)],
            ["가람", "나래", "하늘"],
        )
        self.assertEqual(
            [
                row.student.name
                for row in video_repo.get_enrollments_for_lecture_active(lecture)
            ],
            ["가람", "나래", "하늘"],
        )
        self.assertEqual(
            [
                row.enrollment.student.name
                for row in session_enrollments_for_tenant(self.tenant)
            ],
            ["가람", "나래", "하늘"],
        )
