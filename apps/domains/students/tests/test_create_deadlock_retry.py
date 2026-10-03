from __future__ import annotations

import secrets
import threading
import unittest
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import OperationalError, close_old_connections, connection, transaction
from django.test import Client, TransactionTestCase, override_settings
from rest_framework_simplejwt.tokens import AccessToken

from apps.core.models import Tenant, TenantMembership
from apps.core.services.account_credentials import remember_account_password
from apps.domains.parents.test_support import create_parent_account_fixture
from apps.domains.students.models import Student
from apps.domains.students.views import student_views


def _database_error(state, *, attribute="sqlstate"):
    cause = Exception("synthetic database failure")
    setattr(cause, attribute, state)
    error = OperationalError("synthetic database failure")
    error.__cause__ = cause
    return error


class _CreateFixture:
    def setUp(self):
        super().setUp()
        self.tenant = Tenant.objects.create(code="qa-create-retry", name="Create Retry")
        self.parent_phone = "01098765432"
        self.parent_secret = secrets.token_urlsafe(24)
        self.student_secret = secrets.token_urlsafe(24)
        User = get_user_model()
        self.parent = create_parent_account_fixture(
            tenant=self.tenant, parent_phone=self.parent_phone,
            student_name="QA", initial_password=self.parent_secret,
        ).parent
        self.parent_user = self.parent.user
        actor = User.objects.create_user(
            username="qa-create-admin", tenant=self.tenant, is_staff=True, is_superuser=True,
        )
        TenantMembership.ensure_active(tenant=self.tenant, user=actor, role="admin")
        token = AccessToken.for_user(actor)
        token["tenant_id"] = self.tenant.pk
        token["token_version"] = actor.token_version
        self.headers = {
            "HTTP_HOST": "api.hakwonplus.com",
            "HTTP_X_TENANT_CODE": self.tenant.code,
            "HTTP_AUTHORIZATION": "Bearer " + str(token),
        }
        self.body = {
            "name": "QA Retry Student", "ps_number": "RETRY01", "phone": "01012345555",
            "parent_phone": self.parent_phone, "initial_password_mode": "fixed",
            "initial_password": self.student_secret, "parent_initial_password_mode": "fixed",
            "parent_initial_password": secrets.token_urlsafe(24),
        }

    def _post(self, *, raise_errors=True):
        return Client(raise_request_exception=raise_errors).post(
            "/api/v1/students/", data=self.body, content_type="application/json", **self.headers,
        )

    def _assert_persisted(self, response, parent_secret):
        self.assertEqual(response.status_code, 201)
        student = Student.objects.get(pk=response.json()["id"], tenant=self.tenant)
        self.assertEqual(student.parent_id, self.parent.pk)
        self.assertEqual(student.parent_phone, self.parent_phone)
        self.assertTrue(student.user.check_password(self.student_secret))
        self.assertTrue(student.pending_account_notice_student_password_ciphertext)
        self.assertTrue(student.pending_account_notice_parent_password_ciphertext)
        self.parent.refresh_from_db()
        self.parent_user.refresh_from_db()
        self.assertEqual(self.parent.user_id, self.parent_user.pk)
        self.assertTrue(self.parent_user.check_password(parent_secret))
        self.assertEqual(Student.objects.filter(tenant=self.tenant).count(), 1)
        self.assertEqual(TenantMembership.objects.filter(
            tenant=self.tenant, user=student.user, role="student", is_active=True,
        ).count(), 1)
        readback = Client().get(f"/api/v1/students/{student.pk}/", **self.headers)
        self.assertEqual(readback.status_code, 200)
        self.assertEqual(readback.json()["id"], student.pk)


_HTTP_SETTINGS = {
    "MIDDLEWARE": [
        "apps.core.middleware.safe_method_write.SafeMethodDatabaseWriteMiddleware",
        "apps.core.middleware.tenant.TenantMiddleware",
    ],
    "CACHES": {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}},
}


@override_settings(**_HTTP_SETTINGS)
class StudentCreateDeadlockRetryTests(_CreateFixture, TransactionTestCase):
    def test_deadlock_retries_after_whole_graph_and_commit_callbacks_rollback(self):
        original_create = student_views.create_student_account
        attempts = []
        committed_callbacks = []
        first_ids = []

        def create_then_fail_once(**kwargs):
            attempts.append(kwargs["student_data"]["ps_number"])
            if len(attempts) == 2:
                self.assertFalse(Student.objects.filter(pk=first_ids[0]).exists())
                self.assertFalse(get_user_model().objects.filter(pk=first_ids[1]).exists())
                self.assertEqual(committed_callbacks, [])
            result = original_create(**kwargs)
            attempt = len(attempts)
            transaction.on_commit(lambda: committed_callbacks.append(attempt))
            if attempt == 1:
                first_ids.extend((result.student.pk, result.user.pk))
                raise _database_error("40P01")
            return result

        with patch.object(student_views, "create_student_account", side_effect=create_then_fail_once):
            response = self._post()
        self.assertEqual(attempts, ["RETRY01", "RETRY01"])
        self.assertEqual(committed_callbacks, [2])
        self._assert_persisted(response, self.parent_secret)

    def test_psycopg2_pgcode_also_retries_once(self):
        original_create = student_views.create_student_account
        with patch.object(student_views, "create_student_account", side_effect=[
            _database_error("40P01", attribute="pgcode"), unittest.mock.DEFAULT,
        ], wraps=original_create) as create:
            response = self._post()
        self.assertEqual(create.call_count, 2)
        self._assert_persisted(response, self.parent_secret)

    def test_other_sqlstates_and_missing_direct_cause_are_not_retried(self):
        errors = [_database_error(state) for state in ("55P03", "40001", None)]
        errors.append(OperationalError("synthetic database failure"))
        for error in errors:
            with self.subTest(sqlstate=getattr(error.__cause__, "sqlstate", None)):
                with patch.object(student_views, "create_student_account", side_effect=error) as create:
                    with self.assertRaises(OperationalError) as raised:
                        self._post()
                self.assertIs(raised.exception, error)
                self.assertEqual(create.call_count, 1)
                self.assertFalse(Student.objects.filter(tenant=self.tenant).exists())

    def test_second_deadlock_propagates_without_partial_writes(self):
        attempts = []
        callbacks = []

        def fail(**kwargs):
            attempt = len(attempts) + 1
            get_user_model().objects.create_user(username=f"qa-failed-{attempt}", tenant=self.tenant)
            attempts.append(attempt)
            transaction.on_commit(lambda: callbacks.append(attempt))
            raise _database_error("40P01")

        with patch.object(student_views, "create_student_account", side_effect=fail):
            with self.assertRaises(OperationalError):
                self._post()
        self.assertEqual(attempts, [1, 2])
        self.assertEqual(callbacks, [])
        self.assertFalse(get_user_model().objects.filter(username__startswith="qa-failed-").exists())
        self.assertFalse(Student.objects.filter(tenant=self.tenant).exists())

    def test_callers_outer_transaction_disables_retry(self):
        error = _database_error("40P01")
        with transaction.atomic():
            with patch.object(student_views, "create_student_account", side_effect=error) as create:
                with self.assertRaises(OperationalError) as raised:
                    self._post()
            self.assertIs(raised.exception, error)
            self.assertEqual(create.call_count, 1)
            self.assertFalse(Student.objects.filter(tenant=self.tenant).exists())

    def test_non_database_exception_is_not_retried(self):
        error = RuntimeError("synthetic account failure")
        with patch.object(student_views, "create_student_account", side_effect=error) as create:
            with self.assertRaises(RuntimeError) as raised:
                self._post()
        self.assertIs(raised.exception, error)
        self.assertEqual(create.call_count, 1)


@override_settings(**_HTTP_SETTINGS)
class StudentCreateMixedParentPostgresTests(_CreateFixture, TransactionTestCase):
    @classmethod
    def setUpClass(cls):
        if connection.vendor != "postgresql":
            raise unittest.SkipTest("PostgreSQL is required for mixed Parent/User lock verification.")
        super().setUpClass()

    def test_legacy_parent_first_and_current_user_first_automatically_recover(self):
        User = get_user_model()
        self.parent_user.set_unusable_password()
        self.parent_user.save(update_fields=["password"])
        legacy_secret = secrets.token_urlsafe(24)
        parent_locked = threading.Event()
        user_locked = threading.Event()
        attempts = []
        errors = []
        outcomes = {}
        pids = {}
        committed_callbacks = []
        original_create = student_views.create_student_account

        def configure(role, deadlock_timeout):
            with connection.cursor() as cursor:
                cursor.execute("SET lock_timeout = '8s'")
                cursor.execute("SET statement_timeout = '12s'")
                cursor.execute("SET deadlock_timeout = %s", [deadlock_timeout])
                cursor.execute("SELECT pg_backend_pid()")
                pids[role] = cursor.fetchone()[0]

        def legacy():
            close_old_connections()
            try:
                configure("legacy", "2s")
                with transaction.atomic():
                    type(self.parent).objects.select_for_update().get(pk=self.parent.pk)
                    parent_locked.set()
                    if not user_locked.wait(15):
                        raise AssertionError("current User lock was not observed")
                    user = User.objects.get(pk=self.parent_user.pk)
                    user.set_password(legacy_secret)
                    user.must_change_password = True
                    user.save(update_fields=["password", "must_change_password"])
                    remember_account_password(user, legacy_secret)
                    TenantMembership.ensure_active(tenant=self.tenant, user=user, role="parent")
                outcomes["legacy"] = "committed"
            except Exception as exc:
                errors.append(("legacy", type(exc).__name__))
            finally:
                connection.close()

        def trace_create(**kwargs):
            attempt = len(attempts) + 1
            attempts.append(attempt)
            User.objects.create_user(username=f"qa-mixed-attempt-{attempt}", tenant=self.tenant)
            transaction.on_commit(lambda: committed_callbacks.append(attempt))
            try:
                return original_create(**kwargs)
            except OperationalError as exc:
                outcomes["first_sqlstate"] = getattr(exc.__cause__, "pgcode", None)
                raise

        def observe(execute, sql, params, many, context):
            result = execute(sql, params, many, context)
            if (User._meta.db_table in sql and "FOR UPDATE" in sql.upper()
                    and self.parent_user.pk in (params or ())):
                user_locked.set()
            return result

        def current():
            close_old_connections()
            try:
                configure("current", "100ms")
                if not parent_locked.wait(15):
                    raise AssertionError("legacy Parent lock was not observed")
                with connection.execute_wrapper(observe):
                    outcomes["response"] = self._post(raise_errors=False)
            except Exception as exc:
                errors.append(("current", type(exc).__name__))
            finally:
                connection.close()

        threads = [threading.Thread(target=legacy), threading.Thread(target=current)]
        with patch.object(student_views, "create_student_account", side_effect=trace_create):
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(30)
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        self.assertEqual(len(set(pids.values())), 2)
        self.assertEqual(outcomes.get("legacy"), "committed")
        self.assertEqual(outcomes.get("first_sqlstate"), "40P01")
        self.assertEqual(attempts, [1, 2])
        self.assertFalse(User.objects.filter(username="qa-mixed-attempt-1").exists())
        self.assertEqual(User.objects.filter(username="qa-mixed-attempt-2").count(), 1)
        self.assertEqual(committed_callbacks, [2])
        self._assert_persisted(outcomes["response"], legacy_secret)
