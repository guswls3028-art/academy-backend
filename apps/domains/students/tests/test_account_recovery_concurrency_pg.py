from __future__ import annotations

import threading
import unittest
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import check_password
from django.db import close_old_connections, connection
from django.test import TransactionTestCase

from apps.core.models import PendingPasswordReset, Tenant
from apps.core.models.user import user_internal_username
from apps.domains.students.models import Student
from apps.domains.students.services.account_recovery import (
    RecoveryAccount,
    send_password_recovery,
)


pytestmark = pytest.mark.django_db(transaction=True)


class AccountRecoveryConcurrencyPostgresTests(TransactionTestCase):
    @classmethod
    def setUpClass(cls):
        if connection.vendor != "postgresql":
            raise unittest.SkipTest("PostgreSQL is required for recovery row-lock verification.")
        super().setUpClass()

    @patch(
        "apps.domains.students.services.account_recovery._account_recovery_delivery_disabled",
        return_value=False,
    )
    def test_same_account_recovery_requests_are_serialized(self, _delivery_disabled):
        tenant = Tenant.objects.create(name="Recovery Race", code="recovery-race")
        user = get_user_model().objects.create_user(
            username=user_internal_username(tenant, "student1"),
            password="oldpw123",
            tenant=tenant,
        )
        student = Student.objects.create(
            tenant=tenant,
            user=user,
            ps_number="student1",
            omr_code="11112222",
            name="학생",
            phone="01011112222",
            parent_phone="01033334444",
        )
        first_in_delivery = threading.Event()
        release_first = threading.Event()
        second_in_delivery = threading.Event()
        errors: list[Exception] = []

        def fake_delivery(**kwargs):
            password = kwargs["replacements"]["임시비밀번호"]
            if password == "11112222":
                first_in_delivery.set()
                release_first.wait(timeout=10)
            else:
                second_in_delivery.set()
            return True

        def worker(password: str):
            close_old_connections()
            try:
                thread_student = Student.objects.select_related("user").get(pk=student.pk)
                account = RecoveryAccount(
                    target="student",
                    student=thread_student,
                    user=thread_student.user,
                    send_to=thread_student.phone,
                    display_name=thread_student.name,
                    display_username=thread_student.ps_number,
                )
                send_password_recovery(account, temp_password=password)
            except Exception as exc:
                errors.append(exc)
            finally:
                close_old_connections()

        with patch(
            "apps.domains.students.services.account_recovery._send_owner_alimtalk",
            side_effect=fake_delivery,
        ):
            first = threading.Thread(target=worker, args=("11112222",))
            second = threading.Thread(target=worker, args=("33334444",))
            first.start()
            self.assertTrue(first_in_delivery.wait(timeout=10))
            second.start()
            self.assertFalse(second_in_delivery.wait(timeout=0.5))
            release_first.set()
            first.join(timeout=15)
            second.join(timeout=15)

        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertEqual(errors, [])
        self.assertTrue(second_in_delivery.is_set())
        pending = PendingPasswordReset.objects.get(user=user)
        self.assertTrue(check_password("33334444", pending.password_hash))
        self.assertFalse(check_password("11112222", pending.password_hash))
