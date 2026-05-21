from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.domains.exams.models import AnswerKey, Exam
from apps.domains.exams.views.answer_key_view import AnswerKeyViewSet


User = get_user_model()


class AnswerKeyViewTenantScopeTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant_a = Tenant.objects.create(name="Tenant A", code="ak-a", is_active=True)
        self.tenant_b = Tenant.objects.create(name="Tenant B", code="ak-b", is_active=True)
        self.user_a = User.objects.create_user(
            username="ak_admin_a",
            password="pw1234",
            tenant=self.tenant_a,
            is_staff=True,
        )
        TenantMembership.ensure_active(tenant=self.tenant_a, user=self.user_a, role="admin")

        self.template_a = Exam.objects.create(
            tenant=self.tenant_a,
            title="Template A",
            exam_type=Exam.ExamType.TEMPLATE,
        )
        self.template_b = Exam.objects.create(
            tenant=self.tenant_b,
            title="Template B",
            exam_type=Exam.ExamType.TEMPLATE,
        )
        self.regular_from_template_a = Exam.objects.create(
            tenant=self.tenant_a,
            title="Regular A",
            exam_type=Exam.ExamType.REGULAR,
            template_exam=self.template_a,
        )

    def _items(self, data):
        if isinstance(data, dict) and "results" in data:
            return data["results"]
        return data

    def _request(self, method: str, action: str, *, data=None, pk=None, query=""):
        path = "/api/v1/exams/answer-keys/"
        if pk is not None:
            path = f"{path}{pk}/"
        if query:
            path = f"{path}?{query}"

        request_method = getattr(self.factory, method)
        request = request_method(path, data=data or {}, format="json")
        force_authenticate(request, user=self.user_a)
        request.tenant = self.tenant_a

        method_map = {method: action}
        view = AnswerKeyViewSet.as_view(method_map)
        kwargs = {"pk": pk} if pk is not None else {}
        return view(request, **kwargs)

    def test_list_never_returns_other_tenant_answer_keys(self):
        own = AnswerKey.objects.create(exam=self.template_a, answers={"1": "A"})
        other = AnswerKey.objects.create(exam=self.template_b, answers={"1": "B"})

        response = self._request("get", "list")

        self.assertEqual(response.status_code, 200, response.data)
        ids = {item["id"] for item in self._items(response.data)}
        self.assertIn(own.id, ids)
        self.assertNotIn(other.id, ids)

    def test_foreign_exam_filter_returns_empty_result(self):
        AnswerKey.objects.create(exam=self.template_b, answers={"1": "B"})

        response = self._request("get", "list", query=f"exam={self.template_b.id}")

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(self._items(response.data), [])

    def test_regular_exam_filter_resolves_to_template_answer_key(self):
        answer_key = AnswerKey.objects.create(exam=self.template_a, answers={"1": "A"})

        response = self._request(
            "get",
            "list",
            query=f"exam={self.regular_from_template_a.id}",
        )

        self.assertEqual(response.status_code, 200, response.data)
        items = self._items(response.data)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["id"], answer_key.id)
        self.assertEqual(items[0]["exam"], self.template_a.id)

    def test_create_rejects_other_tenant_exam_id(self):
        response = self._request(
            "post",
            "create",
            data={"exam": self.template_b.id, "answers": {"1": "B"}},
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(AnswerKey.objects.filter(exam=self.template_b).exists())

    def test_update_rejects_cross_tenant_exam_move(self):
        answer_key = AnswerKey.objects.create(exam=self.template_a, answers={"1": "A"})

        response = self._request(
            "patch",
            "partial_update",
            pk=answer_key.id,
            data={"exam": self.template_b.id},
        )

        self.assertEqual(response.status_code, 400, response.data)
        answer_key.refresh_from_db()
        self.assertEqual(answer_key.exam_id, self.template_a.id)

    def test_create_rejects_regular_exam_that_uses_template(self):
        response = self._request(
            "post",
            "create",
            data={"exam": self.regular_from_template_a.id, "answers": {"1": "A"}},
        )

        self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(AnswerKey.objects.filter(exam=self.regular_from_template_a).exists())

    def test_update_rejects_answer_key_for_used_template(self):
        answer_key = AnswerKey.objects.create(exam=self.template_a, answers={"1": "A"})

        response = self._request(
            "patch",
            "partial_update",
            pk=answer_key.id,
            data={"answers": {"1": "C"}},
        )

        self.assertEqual(response.status_code, 400, response.data)
        answer_key.refresh_from_db()
        self.assertEqual(answer_key.answers, {"1": "A"})

    def test_destroy_rejects_answer_key_for_used_template(self):
        answer_key = AnswerKey.objects.create(exam=self.template_a, answers={"1": "A"})

        response = self._request("delete", "destroy", pk=answer_key.id)

        self.assertEqual(response.status_code, 400, response.data)
        self.assertTrue(AnswerKey.objects.filter(id=answer_key.id).exists())
