"""Student detail Q&A history: authorship, visibility and page boundaries."""
from django.contrib.auth import get_user_model
from rest_framework.test import APIRequestFactory, force_authenticate
from django.test import TestCase

from apps.core.models import Tenant, TenantMembership
from apps.domains.students.test_support import create_student_fixture
from apps.domains.parents.test_support import create_parent_account_fixture
from apps.domains.community.models import PostEntity
from apps.domains.community.api.views.post_views import PostViewSet


class StudentQuestionHistoryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(code="history-a", name="History A")
        cls.foreign_tenant = Tenant.objects.create(code="history-b", name="History B")
        cls.staff = get_user_model().objects.create_user(
            username="history-staff", tenant=cls.tenant, password="test",
        )
        TenantMembership.ensure_active(tenant=cls.tenant, user=cls.staff, role="owner")
        cls.students = []
        for index, tenant in enumerate((cls.tenant, cls.tenant, cls.foreign_tenant), start=1):
            user = get_user_model().objects.create_user(
                username=f"history-student-{index}", tenant=tenant, password="test",
            )
            TenantMembership.ensure_active(tenant=tenant, user=user, role="student")
            cls.students.append(create_student_fixture(
                tenant=tenant, user=user, name=f"Student {index}",
                ps_number=f"H{index}", phone=f"0101234000{index}",
                parent_phone=f"0105678000{index}", omr_code=f"1234000{index}",
            ))
        cls.student, cls.other, cls.foreign = cls.students
        cls.parent = create_parent_account_fixture(
            tenant=cls.tenant, parent_phone=cls.student.parent_phone,
            student_name=cls.student.name, initial_password="test-password",
        ).parent
        cls.parent_user = cls.parent.user
        cls.student.parent = cls.parent
        cls.student.save(update_fields=["parent"])
        cls.own_post = cls.post(cls.student)
        cls.other_post = cls.post(cls.other)
        cls.foreign_post = cls.post(cls.foreign)
        cls.post(cls.student, post_type="board")
        PostEntity.objects.create(
            tenant=cls.tenant, post_type="qna", title="Staff question",
            content="Test", author_role="staff", status="published",
        )

    @classmethod
    def post(cls, student, **overrides):
        values = dict(
            tenant=student.tenant, created_by=student, post_type="qna",
            title="Question", content="Test", author_role="student", status="published",
        )
        values.update(overrides)
        return PostEntity.objects.create(**values)

    def get(self, author, *, user=None, page=1, page_size=50):
        request = APIRequestFactory().get("/api/v1/community/posts/", {
            "author_student": author, "post_type": "qna", "page": page, "page_size": page_size,
        })
        request.tenant = self.tenant
        if user == self.parent_user:
            request.META["HTTP_X_STUDENT_ID"] = str(self.student.pk)
        force_authenticate(request, user=user or self.staff)
        return PostViewSet.as_view({"get": "list"})(request)

    def ids(self, response):
        self.assertEqual(response.status_code, 200, response.data)
        rows = response.data if isinstance(response.data, list) else response.data["results"]
        return [row["id"] for row in rows]

    def test_staff_history_contains_only_selected_students_questions(self):
        self.assertEqual(self.ids(self.get(self.student.pk)), [self.own_post.pk])
        self.assertEqual(self.ids(self.get(self.other.pk)), [self.other_post.pk])

    def test_foreign_or_missing_author_never_widens_history(self):
        self.assertEqual(self.ids(self.get(self.foreign.pk)), [])
        self.assertEqual(self.ids(self.get(999999)), [])

    def test_student_and_parent_filters_intersect_existing_visibility(self):
        for user in (self.student.user, self.parent_user):
            with self.subTest(user=user.pk):
                self.assertEqual(self.ids(self.get(self.student.pk, user=user)), [self.own_post.pk])
                self.assertEqual(self.ids(self.get(self.other.pk, user=user)), [])
                self.assertEqual(self.ids(self.get(self.foreign.pk, user=user)), [])

    def test_invalid_author_is_rejected_instead_of_returning_every_student(self):
        for value in ("", "0", "-1", "invalid", "1.5", "9223372036854775808"):
            with self.subTest(value=value):
                response = self.get(value)
                self.assertEqual(response.status_code, 400, response.data)
                self.assertIn("author_student", response.data)

    def test_pagination_preserves_author_filter_and_total(self):
        for _ in range(51):
            self.post(self.student)
        first = self.get(self.student.pk)
        second = self.get(self.student.pk, page=2)
        first_ids, second_ids = self.ids(first), self.ids(second)
        self.assertEqual(first.data["count"], 52)
        self.assertEqual(len(first_ids), 50)
        self.assertEqual(len(second_ids), 2)
        self.assertFalse(set(first_ids) & set(second_ids))
        self.assertNotIn(self.other_post.pk, first_ids + second_ids)

    def test_inconsistent_cross_tenant_author_relation_is_not_exposed(self):
        self.post(self.foreign, tenant=self.tenant)
        self.assertEqual(self.ids(self.get(self.foreign.pk)), [])
