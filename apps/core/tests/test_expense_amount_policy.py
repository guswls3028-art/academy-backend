from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Expense, Tenant, TenantMembership
from apps.core.services.expense_policy import normalize_expense_amount
from apps.core.views.expense import MyExpenseViewSet


User = get_user_model()


class ExpenseAmountPolicyTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = Tenant.objects.create(
            name="Expense Amount Tenant",
            code="expense-amount",
            is_active=True,
        )
        self.user = User.objects.create_user(
            username="expense_amount_staff",
            password="test1234",
            tenant=self.tenant,
            is_staff=True,
        )
        TenantMembership.ensure_active(tenant=self.tenant, user=self.user, role="teacher")

    def _request(self, method: str, action: str, *, data=None, pk=None):
        path = "/api/v1/core/profile/expenses/"
        if pk is not None:
            path = f"{path}{pk}/"
        request_method = getattr(self.factory, method)
        request = request_method(path, data=data or {}, format="json")
        request.tenant = self.tenant
        force_authenticate(request, user=self.user)
        view = MyExpenseViewSet.as_view({method: action})
        kwargs = {"pk": pk} if pk is not None else {}
        return view(request, **kwargs)

    def test_normalize_expense_amount_rejects_zero_negative_and_blank_values(self):
        self.assertEqual(normalize_expense_amount("1200"), 1200)
        for bad_value in ("", "0", "-1", None):
            with self.subTest(value=bad_value):
                with self.assertRaises(ValueError):
                    normalize_expense_amount(bad_value)

    def test_create_rejects_non_positive_expense_amount(self):
        for amount in (0, -100):
            with self.subTest(amount=amount):
                response = self._request(
                    "post",
                    "create",
                    data={"date": "2026-06-25", "title": "교재", "amount": amount},
                )

                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, response.data)

        self.assertFalse(Expense.objects.exists())

    def test_update_rejects_non_positive_expense_amount_without_mutating_existing_record(self):
        expense = Expense.objects.create(
            tenant=self.tenant,
            user=self.user,
            date="2026-06-25",
            title="교통비",
            amount=1000,
        )

        response = self._request(
            "patch",
            "partial_update",
            pk=expense.id,
            data={"amount": -500},
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, response.data)
        expense.refresh_from_db()
        self.assertEqual(expense.amount, 1000)
