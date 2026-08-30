from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.utils import timezone
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory, force_authenticate

from academy.adapters.db.django import repositories_video as video_repo
from apps.core.models.tenant import Tenant
from apps.core.models.tenant_membership import TenantMembership
from apps.domains.enrollment.selectors import (
    active_session_enrollments_for_session,
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
            omr_code=f"88{suffix:06d}",
            name=name,
            phone=f"0101{suffix:07d}",
            parent_phone=f"0102{suffix:07d}",
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

    def test_postgres_korean_codepoint_order_is_shared_by_all_rosters(self):
        if connection.vendor != "postgresql":
            self.skipTest("PostgreSQL collation contract")

        probes = [
            self._create_student(name, suffix, grade=1)
            for suffix, name in enumerate(
                ("간", "가가", "각", "가", "나", "가가"),
                start=10,
            )
        ]
        expected_student_ids = [
            row.id
            for row in sorted(
                [self.haneul, self.garam_first, self.narae, self.garam_second, *probes],
                key=lambda row: (row.name, row.id),
            )
        ]

        request = Request(self.factory.get("/api/v1/students/"))
        request.tenant = self.tenant
        view = StudentViewSet()
        view.request = request
        view.action = "list"
        view.args = ()
        view.kwargs = {}
        queryset = view.filter_queryset(view.get_queryset())

        self.assertIn('COLLATE "C"', str(queryset.query))
        self.assertEqual(
            list(queryset.values_list("id", flat=True)),
            expected_student_ids,
        )

        lecture = create_lecture_fixture(
            tenant=self.tenant,
            title="PostgreSQL 학생 정렬 강의",
            name="PostgreSQL 학생 정렬 강의",
            subject="수학",
        )
        session = create_session_fixture(
            lecture=lecture,
            order=1,
            title="PostgreSQL 정렬 차시",
        )
        enrollments = [
            create_enrollment_fixture(
                tenant=self.tenant,
                student=student,
                lecture=lecture,
            )
            for student in probes
        ]
        session_enrollments = [
            create_session_enrollment_fixture(
                tenant=self.tenant,
                session=session,
                enrollment=enrollment,
            )
            for enrollment in enrollments
        ]
        expected_enrollment_ids = [
            enrollment.id
            for _, enrollment in sorted(
                zip(probes, enrollments, strict=True),
                key=lambda pair: (pair[0].name, pair[0].id, pair[1].id),
            )
        ]
        expected_session_ids = [
            session_enrollment.id
            for student, enrollment, session_enrollment in sorted(
                zip(probes, enrollments, session_enrollments, strict=True),
                key=lambda row: (row[0].name, row[1].id, row[2].id),
            )
        ]

        querysets = (
            (enrollments_for_tenant(self.tenant), expected_enrollment_ids),
            (session_enrollments_for_tenant(self.tenant), expected_session_ids),
            (
                active_session_enrollments_for_session(
                    tenant=self.tenant,
                    session_id=session.id,
                ),
                expected_session_ids,
            ),
            (
                video_repo.get_enrollments_for_lecture_active(lecture),
                expected_enrollment_ids,
            ),
        )
        for roster_queryset, expected_ids in querysets:
            with self.subTest(model=roster_queryset.model.__name__):
                self.assertIn('COLLATE "C"', str(roster_queryset.query))
                self.assertEqual(
                    list(roster_queryset.values_list("id", flat=True)),
                    expected_ids,
                )
