from __future__ import annotations

import threading
import unittest

import pytest
from django.contrib.auth import get_user_model
from django.db import close_old_connections, connection
from django.test import TestCase, TransactionTestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.core.models.user import user_internal_username
from apps.core.services.password import CurrentPasswordMismatch, change_password_with_notice
from apps.core.views.auth import ChangePasswordView
from apps.domains.students.models import Student


class PasswordChangeRollbackTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="Password Atomic", code="password-atomic")
        self.user = get_user_model().objects.create_user(
            username=user_internal_username(self.tenant, "student1"),
            password="oldpw123",
            tenant=self.tenant,
            must_change_password=True,
            token_version=0,
        )
        TenantMembership.ensure_active(tenant=self.tenant, user=self.user, role="student")
        Student.objects.create(
            tenant=self.tenant,
            user=self.user,
            ps_number="student1",
            omr_code="11112222",
            name="학생",
            phone="01011112222",
            parent_phone="01033334444",
        )

    def test_notice_failure_rolls_back_password_and_all_session_state(self):
        request = APIRequestFactory().post(
            "/api/v1/core/change-password/",
            {"old_password": "oldpw123", "new_password": "newpw123"},
            format="json",
        )
        request.tenant = self.tenant
        force_authenticate(request, user=self.user)

        from unittest.mock import patch
        with patch("apps.domains.messaging.policy.send_alimtalk_via_owner", return_value=False):
            response = ChangePasswordView.as_view()(request)

        self.assertEqual(response.status_code, 503)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("oldpw123"))
        self.assertTrue(self.user.must_change_password)
        self.assertEqual(self.user.token_version, 0)


pytestmark = pytest.mark.django_db(transaction=True)


class PasswordChangeConcurrencyPostgresTests(TransactionTestCase):
    @classmethod
    def setUpClass(cls):
        if connection.vendor != "postgresql":
            raise unittest.SkipTest("PostgreSQL is required for password row-lock verification.")
        super().setUpClass()

    def test_same_old_password_can_commit_only_once(self):
        tenant = Tenant.objects.create(name="Password Race", code="password-race")
        user = get_user_model().objects.create_user(
            username=user_internal_username(tenant, "teacher1"),
            password="oldpw123",
            tenant=tenant,
            token_version=0,
        )
        barrier = threading.Barrier(2, timeout=10)
        outcomes: list[str] = []

        def worker(new_password: str):
            close_old_connections()
            try:
                thread_user = get_user_model().objects.get(pk=user.pk)
                barrier.wait()
                change_password_with_notice(
                    thread_user,
                    current_password="oldpw123",
                    new_password=new_password,
                    send_notice=lambda **_kwargs: True,
                )
                outcomes.append("changed")
            except CurrentPasswordMismatch:
                outcomes.append("stale")
            finally:
                close_old_connections()

        threads = [
            threading.Thread(target=worker, args=("first-new-password",)),
            threading.Thread(target=worker, args=("second-new-password",)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)

        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertCountEqual(outcomes, ["changed", "stale"])
        user.refresh_from_db()
        self.assertEqual(user.token_version, 1)
        self.assertTrue(
            user.check_password("first-new-password")
            or user.check_password("second-new-password")
        )
