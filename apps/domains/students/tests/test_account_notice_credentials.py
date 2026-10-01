from threading import Event, Thread
from unittest import skipUnless
from django.db import connection, connections, transaction
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, TransactionTestCase
from rest_framework.test import APIClient, APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantDomain, TenantMembership, PendingPasswordReset
from apps.core.services.account_credentials import account_notice_password
from apps.core.services.initial_password_policy import save_password_settings
from apps.core.services.password import change_password, consume_pending_password_reset, pending_password_reset_matches
from apps.domains.students.services.creation import create_student_account
from apps.domains.students.services.account_notice import _decrypt, dispatch_pending_account_notice
from apps.domains.students.services.account_notifications import send_parent_account_credentials_notice
from apps.domains.students.views.student_views import StudentViewSet
from apps.domains.students.views.initial_password_settings import InitialPasswordSettingsView


class AccountNoticeCredentialTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="초기 비밀번호 QA", code="passwordqa")
        TenantDomain.objects.filter(tenant=self.tenant, is_primary=True).update(is_primary=False)
        TenantDomain.objects.create(tenant=self.tenant, host="passwordqa.test", is_primary=True)
        self.client = APIClient(HTTP_HOST="passwordqa.test")
        self.factory = APIRequestFactory()
        self.admin = get_user_model().objects.create_user(username="passwordqa-admin", password="admin1234", tenant=self.tenant, is_staff=True)
        TenantMembership.ensure_active(tenant=self.tenant, user=self.admin, role="owner")

    def create(self, suffix="01", **kwargs):
        return create_student_account(tenant=self.tenant, student_data={"name": f"학생{suffix}", "ps_number": f"student{suffix}", "phone": f"010778899{suffix}", "parent_phone": "01033445566"}, **kwargs)

    def usable(self, user, password):
        self.assertTrue(user.check_password(password) or pending_password_reset_matches(user, password))
        self.assertGreaterEqual(len(password), 4)
        self.assertNotIn(password, ("변경되지 않음", "가입 신청 시 입력한 비밀번호"))

    def test_new_student_and_parent_use_their_own_phone_suffix(self):
        result = self.create()
        self.assertTrue(result.user.check_password("9901"))
        self.assertTrue(result.parent.user.check_password("5566"))
        self.assertEqual(_decrypt(result.student.pending_account_notice_student_password_ciphertext), "9901")
        self.assertEqual(_decrypt(result.student.pending_account_notice_parent_password_ciphertext), "5566")

    def test_single_registration_accepts_independent_explicit_passwords_without_trimming(self):
        request = self.factory.post("/api/v1/students/", {"name": "단건학생", "phone": "01077889901", "parent_phone": "01033445566", "initial_password": " student1234 ", "parent_initial_password": " parent5678 ", "school_type": "HIGH", "grade": 1}, format="json")
        request.tenant = self.tenant
        force_authenticate(request, user=self.admin)
        response = StudentViewSet.as_view({"post": "create"})(request)
        self.assertEqual(response.status_code, 201, response.data)
        from apps.domains.students.models import Student
        student = Student.objects.get(pk=response.data["id"])
        self.assertTrue(student.user.check_password(" student1234 "))
        self.assertTrue(student.parent.user.check_password(" parent5678 "))
        self.assertNotIn("account_notice_password_ciphertext", str(response.data))

    def test_no_student_phone_generates_a_usable_numeric_password(self):
        result = create_student_account(tenant=self.tenant, student_data={"name": "번호없는학생", "ps_number": "no-phone", "parent_phone": "01033445566"})
        password = _decrypt(result.student.pending_account_notice_student_password_ciphertext)
        self.assertEqual(len(password), 6)
        self.assertTrue(password.isdigit())
        self.usable(result.user, password)

    def test_existing_shared_parent_password_and_both_student_notices_remain_usable(self):
        first = self.create(password="first1234", parent_password="family5678")
        second = self.create("02", password="second1234", parent_password="must-not-replace")
        self.assertEqual(first.parent.pk, second.parent.pk)
        second.parent.user.refresh_from_db()
        self.assertTrue(second.parent.user.check_password("family5678"))
        self.assertEqual(second.parent_password_for_notice, "family5678")
        with patch("apps.domains.students.services.account_notifications._send_owner_account_notice", return_value=True) as send:
            self.assertTrue(send_parent_account_credentials_notice(student=second.student))
        values = send.call_args.kwargs["replacements"]
        self.usable(second.student.user, values["학생비밀번호"])
        self.usable(second.parent.user, values["학부모비밀번호"])

    def test_legacy_unknown_password_notice_preserves_old_login_and_reuses_pending_credential(self):
        result = self.create(password="old1234")
        result.user.account_notice_password_ciphertext = ""
        result.user.save(update_fields=["account_notice_password_ciphertext"])
        password = account_notice_password(result.user)
        result.user.refresh_from_db()
        self.assertTrue(result.user.check_password("old1234"))
        self.assertTrue(pending_password_reset_matches(result.user, password))
        self.assertEqual(account_notice_password(result.user), password)
        from datetime import timedelta
        from django.utils import timezone
        self.assertGreater(PendingPasswordReset.objects.get(user=result.user).expires_at, timezone.now() + timedelta(days=29))
        self.assertTrue(consume_pending_password_reset(result.user, password))
        self.assertTrue(result.user.check_password(password))

    def test_changed_password_replaces_stale_first_enrollment_values(self):
        result = self.create(password="before1234")
        change_password(result.user, "after5678")
        with patch("apps.support.students.account_notice_dependencies.send_welcome_messages", return_value={"status": "enqueued", "enqueued": 2}) as send:
            response = dispatch_pending_account_notice(student_id=result.student.pk)
        self.assertEqual(response["status"], "enqueued")
        self.assertEqual(send.call_args.kwargs["student_password"], "after5678")
        result.student.refresh_from_db()
        self.assertEqual(result.student.pending_account_notice_student_password_ciphertext, "")
        self.assertEqual(account_notice_password(result.user), "after5678")

    def test_policy_saved_values_are_encrypted_and_only_affect_future_accounts(self):
        first = self.create(password="existing1234")
        save_password_settings(self.tenant, {"student_mode": "fixed", "student_fixed_password": "new5678", "parent_mode": "random"})
        self.assertNotIn("new5678", str(self.tenant.account_password_policy))
        second = self.create("02")
        self.assertTrue(second.user.check_password("new5678"))
        first.user.refresh_from_db()
        self.assertTrue(first.user.check_password("existing1234"))

    def test_ciphertext_cannot_be_replayed_for_another_tenant_or_user(self):
        first = self.create(password="private1234")
        other = Tenant.objects.create(name="다른학원", code="other-passwordqa")
        user = get_user_model().objects.create_user(username="other-user", tenant=other, password="other5678")
        first.user.refresh_from_db()
        user.account_notice_password_ciphertext = first.user.account_notice_password_ciphertext
        user.save(update_fields=["account_notice_password_ciphertext"])
        password = account_notice_password(user)
        self.assertNotEqual(password, "private1234")
        self.usable(user, password)

    def test_policy_endpoint_rejects_students_and_anonymous_users(self):
        for user in (None, self.create().user):
            request = self.factory.get("/api/v1/students/account-password-settings/")
            request.tenant = self.tenant
            if user:
                force_authenticate(request, user=user)
            response = InitialPasswordSettingsView.as_view()(request)
            self.assertIn(response.status_code, (401, 403))

    def test_delivered_legacy_student_and_parent_credentials_complete_real_jwt_login(self):
        result = self.create(password="old1234", parent_password="parent5678")
        result.user.account_notice_password_ciphertext = ""
        result.user.save(update_fields=["account_notice_password_ciphertext"])
        with patch("apps.domains.students.services.account_notifications._send_owner_account_notice", return_value=True) as send:
            send_parent_account_credentials_notice(student=result.student)
        values = send.call_args.kwargs["replacements"]
        self.assertEqual(values["사이트링크"], "https://passwordqa.test")
        for username_key, password_key in (("학생아이디", "학생비밀번호"), ("학부모아이디", "학부모비밀번호")):
            self.client.credentials()
            response = self.client.post("/api/v1/token/", {"username": values[username_key], "password": values[password_key]}, format="json")
            self.assertEqual(response.status_code, 200, response.data)
            self.client.credentials(HTTP_AUTHORIZATION="Bearer " + response.data["access"])
            self.assertEqual(self.client.get("/api/v1/core/me/").status_code, 200)



@skipUnless(connection.vendor == "postgresql", "requires PostgreSQL row locks")
class AccountNoticeLockOrderTests(TransactionTestCase):
    def test_parent_notice_preserves_user_before_student_lock_order(self):
        tenant = Tenant.objects.create(name="계정 잠금 QA", code="password-lock-qa")
        result = create_student_account(tenant=tenant, student_data={
            "name": "잠금검증", "ps_number": "LOCK-QA", "phone": "01070001111", "parent_phone": "01080002222",
        })
        student_id, user_id = result.student.pk, result.user.pk
        user_locked, notice_started = Event(), Event()
        failures = []
        delivered = []

        def lifecycle_locks():
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SET statement_timeout TO 5000")
                with transaction.atomic():
                    get_user_model().objects.select_for_update().get(pk=user_id)
                    user_locked.set()
                    if not notice_started.wait(5):
                        raise RuntimeError("notice did not start")
                    from time import sleep
                    sleep(0.2)
                    from apps.domains.students.models import Student
                    Student.objects.select_for_update().get(pk=student_id)
            except Exception as exc:
                failures.append(exc)
            finally:
                connections.close_all()

        def notice():
            try:
                from apps.domains.students.models import Student
                with connection.cursor() as cursor:
                    cursor.execute("SET statement_timeout TO 5000")
                notice_started.set()
                delivered.append(send_parent_account_credentials_notice(student=Student.objects.get(pk=student_id)))
            except Exception as exc:
                failures.append(exc)
            finally:
                connections.close_all()

        with patch("apps.domains.students.services.account_notifications._send_owner_account_notice", return_value=True):
            first = Thread(target=lifecycle_locks, daemon=True)
            first.start()
            self.assertTrue(user_locked.wait(5))
            second = Thread(target=notice, daemon=True)
            second.start()
            first.join(10)
            second.join(10)
        self.assertFalse(first.is_alive() or second.is_alive())
        self.assertEqual(failures, [])
        self.assertEqual(delivered, [True])
