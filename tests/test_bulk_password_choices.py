"""Explicit student/parent credential choices across bulk and restore transports."""

from __future__ import annotations

import io
from unittest.mock import patch

import openpyxl
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from academy.application.services.excel_parsing_service import ExcelParsingService
from apps.core.models import Tenant, TenantMembership
from apps.core.services.initial_password_policy import save_password_settings
from apps.domains.ai.models import AIJobModel
from apps.domains.ai.services.excel_job_secrets import (
    EXCEL_INITIAL_PASSWORD_SECRET_FIELD,
    EXCEL_PARENT_INITIAL_PASSWORD_SECRET_FIELD,
    ExcelJobSecretError,
    protect_excel_initial_password,
    recover_excel_initial_password,
    scrub_excel_job_payload,
)
from apps.domains.parents.models import Parent
from apps.domains.students.models import Student
from apps.domains.students.services import import_students_from_rows
from apps.domains.students.services.account_notice import _decrypt
from apps.domains.students.services.bulk_from_excel import bulk_create_students_from_excel_rows
from apps.domains.students.services.import_passwords import StudentImportPasswordError
from apps.domains.students.services.import_students import resolve_student_import_conflicts
from apps.domains.students.services.lifecycle import soft_delete_student
from apps.domains.students.views import StudentViewSet


class BulkPasswordEnvelopeTests(SimpleTestCase):
    def test_both_roles_round_trip_raw_whitespace_and_are_scrubbed(self):
        for password in ("  raw password  ", "    ", "excel:v1:literal-password"):
            with self.subTest(password=password):
                payload = {
                    **protect_excel_initial_password(password),
                    **protect_excel_initial_password(password, role="parent"),
                }
                self.assertNotEqual(payload[EXCEL_INITIAL_PASSWORD_SECRET_FIELD], password)
                self.assertNotEqual(payload[EXCEL_PARENT_INITIAL_PASSWORD_SECRET_FIELD], password)
                self.assertEqual(recover_excel_initial_password(payload), password)
                self.assertEqual(recover_excel_initial_password(payload, role="parent"), password)
                payload.update(initial_password=password, parent_initial_password=password)
                self.assertEqual(scrub_excel_job_payload(payload), {})

    def test_corrupt_parent_secret_fails_closed(self):
        with self.assertRaises(ExcelJobSecretError):
            recover_excel_initial_password(
                {EXCEL_PARENT_INITIAL_PASSWORD_SECRET_FIELD: "corrupt"}, role="parent",
            )


class BulkPasswordChoiceTests(TestCase):
    def setUp(self):
        cache.clear()
        self.tenant = Tenant.objects.create(name="선택학원", code="bulk-choice", is_active=True)
        self.row = {
            "name": "선택학생", "phone": "01090001234", "parent_phone": "01070005678",
            "school_type": "HIGH", "grade": 1, "uses_identifier": False,
        }
        self.factory = APIRequestFactory()
        self.admin = get_user_model().objects.create_user(
            username="bulk-choice-admin", tenant=self.tenant, is_staff=True, password="admin-test",
        )
        TenantMembership.ensure_active(tenant=self.tenant, user=self.admin, role="owner")

    def _import(self, *, row=None, **options):
        return import_students_from_rows(
            tenant_id=self.tenant.id, students_data=[row or self.row], **options,
        )

    def _student(self):
        return Student.objects.select_related("user", "parent__user").get(tenant=self.tenant)

    def _assert_empty_graph(self):
        self.assertFalse(Student.objects.filter(tenant=self.tenant).exists())
        self.assertFalse(Parent.objects.filter(tenant=self.tenant).exists())
        self.assertEqual(get_user_model().objects.filter(tenant=self.tenant).count(), 1)
        self.assertEqual(TenantMembership.objects.filter(tenant=self.tenant).count(), 1)

    def _request(self, action, data, *, format="json"):
        request = self.factory.post(f"/api/v1/students/{action}/", data, format=format)
        request.tenant = self.tenant
        force_authenticate(request, user=self.admin)
        return StudentViewSet.as_view({"post": action})(request)

    def _deleted_with_unusable_parent(self):
        result = self._import(initial_password="student-original", parent_initial_password="parent-original")
        self.assertEqual(result["created"], 1)
        student = self._student()
        self.deleted_parent_user = student.parent.user
        self.deleted_parent_user.set_unusable_password()
        self.deleted_parent_user.save(update_fields=["password"])
        soft_delete_student(student, tenant=self.tenant)
        student.refresh_from_db()
        return student

    def test_own_role_phone_last4(self):
        result = self._import(password_mode="phone_last4", parent_initial_password_mode="phone_last4")
        self.assertEqual(result["created"], 1)
        student = self._student()
        self.assertTrue(student.user.check_password("1234"))
        self.assertTrue(student.parent.user.check_password("5678"))

    def test_fixed_preserves_whitespace_for_both_roles(self):
        result = self._import(
            password_mode="fixed", initial_password="  student  ",
            parent_initial_password_mode="fixed", parent_initial_password="    ",
        )
        self.assertEqual(result["created"], 1)
        student = self._student()
        self.assertTrue(student.user.check_password("  student  "))
        self.assertTrue(student.parent.user.check_password("    "))
        self.assertEqual(_decrypt(student.pending_account_notice_student_password_ciphertext), "  student  ")
        self.assertEqual(_decrypt(student.pending_account_notice_parent_password_ciphertext), "    ")

    def test_random_is_six_digits_and_matches_both_accounts(self):
        result = self._import(password_mode="random", parent_initial_password_mode="random")
        student = self._student()
        for role in ("student", "parent"):
            password = _decrypt(getattr(student, f"pending_account_notice_{role}_password_ciphertext"))
            self.assertRegex(password, r"^\d{6}$")
            user = student.user if role == "student" else student.parent.user
            self.assertTrue(user.check_password(password))
        self.assertEqual(result["credentials"][0]["password"], _decrypt(student.pending_account_notice_student_password_ciphertext))

    def test_missing_or_shared_student_phone_fails_without_graph(self):
        for phone in (None, self.row["parent_phone"]):
            with self.subTest(phone=phone):
                result = self._import(
                    row={**self.row, "phone": phone}, password_mode="phone_last4",
                    parent_initial_password_mode="phone_last4",
                )
                self.assertEqual(result["created"], 0)
                self.assertEqual(result["failed"][0]["reason_code"], "password_policy")
                self.assertIn("직접 입력 또는 랜덤", result["failed"][0]["error"])
                self._assert_empty_graph()

    def test_unset_student_policy_fails_before_graph(self):
        with self.assertRaises(StudentImportPasswordError):
            self._import(parent_initial_password_mode="phone_last4")
        self._assert_empty_graph()

    def test_unset_parent_policy_is_row_error_without_partial_graph(self):
        result = self._import(initial_password="student-only")
        self.assertEqual(result["created"], 0)
        self.assertEqual(result["failed"][0]["reason_code"], "password_policy")
        self.assertIn("학부모", result["failed"][0]["error"])
        self._assert_empty_graph()

    def test_explicit_saved_role_policies_support_legacy_caller(self):
        save_password_settings(self.tenant, {"student_mode": "phone_last4", "parent_mode": "phone_last4"})
        result = bulk_create_students_from_excel_rows(tenant_id=self.tenant.id, students_data=[self.row])
        self.assertEqual(result["created"], 1)
        student = self._student()
        self.assertTrue(student.user.check_password("1234"))
        self.assertTrue(student.parent.user.check_password("5678"))

    def test_explicit_fixed_does_not_borrow_stored_fixed_value(self):
        save_password_settings(self.tenant, {"student_mode": "fixed", "student_fixed_password": "stored-pass"})
        with self.assertRaises(StudentImportPasswordError):
            self._import(password_mode="fixed", parent_initial_password_mode="phone_last4")
        self._assert_empty_graph()

    def test_legacy_raw_values_work_without_saved_policies(self):
        result = bulk_create_students_from_excel_rows(
            tenant_id=self.tenant.id, students_data=[self.row],
            initial_password="student-raw", parent_initial_password="parent-raw",
        )
        self.assertEqual(result["created"], 1)
        student = self._student()
        self.assertTrue(student.user.check_password("student-raw"))
        self.assertTrue(student.parent.user.check_password("parent-raw"))

    def test_existing_parent_reused_with_unset_parent_choice(self):
        self._import(initial_password="first-student", parent_initial_password="keep-parent")
        parent = self._student().parent
        original_hash = parent.user.password
        result = self._import(
            row={**self.row, "name": "둘째학생", "phone": "01090009876"}, initial_password="second-student",
        )
        self.assertEqual(result["created"], 1)
        second = Student.objects.get(tenant=self.tenant, name="둘째학생")
        self.assertEqual(second.parent_id, parent.id)
        parent.user.refresh_from_db()
        self.assertEqual(parent.user.password, original_hash)

    def test_json_bulk_accepts_initial_password_mode_alias(self):
        response = self._request("bulk_create", {
            "students": [self.row], "initial_password_mode": "phone_last4",
            "parent_initial_password_mode": "fixed", "parent_initial_password": "  parent  ",
        })
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["created"], 1)
        self.assertTrue(self._student().parent.user.check_password("  parent  "))

    def test_json_bulk_unset_or_conflicting_choices_return_400(self):
        for options in ({}, {"password_mode": "random", "initial_password_mode": "phone_last4"}):
            with self.subTest(options=options):
                response = self._request("bulk_create", {"students": [self.row], **options})
                self.assertEqual(response.status_code, 400, response.data)
                self._assert_empty_graph()

    def test_conflict_restore_never_borrows_student_password_for_parent(self):
        student = self._deleted_with_unusable_parent()
        result = resolve_student_import_conflicts(
            tenant=self.tenant, initial_password="must-not-be-parent",
            resolutions=[{"row": 1, "student_id": student.id, "action": "restore", "student_data": self.row}],
        )
        self.assertEqual(result["restored"], 0)
        self.assertIn("학부모", result["failed"][0]["error"])
        student.refresh_from_db()
        self.deleted_parent_user.refresh_from_db()
        self.assertIsNotNone(student.deleted_at)
        self.assertFalse(self.deleted_parent_user.has_usable_password())

    @patch("apps.domains.students.views.student_views.send_parent_account_credentials_notice", return_value=True)
    def test_bulk_restore_parent_choice_preserves_student_credentials(self, send_notice):
        student = self._deleted_with_unusable_parent()
        student_hash = student.user.password
        response = self._request("bulk_restore", {
            "ids": [student.id], "initial_password_mode": "random",
            "parent_initial_password_mode": "fixed", "parent_initial_password": "  restored-parent  ",
        })
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["restored"], 1, response.data)
        student.refresh_from_db()
        self.assertIsNone(student.deleted_at)
        self.assertEqual(student.user.password, student_hash)
        self.assertTrue(student.parent.user.check_password("  restored-parent  "))
        self.assertEqual(send_notice.call_args.kwargs["parent_password"], "  restored-parent  ")

    @patch("apps.domains.students.services.import_students.send_parent_account_credentials_notice", return_value=True)
    def test_import_restore_forwards_parent_phone_choice(self, send_notice):
        student = self._deleted_with_unusable_parent()
        result = self._import(initial_password="unused-on-restore", parent_initial_password_mode="phone_last4")
        self.assertEqual(result["restored"][0]["student_id"], student.id)
        student.refresh_from_db()
        self.assertTrue(student.parent.user.check_password("5678"))
        self.assertEqual(send_notice.call_args.kwargs["parent_password"], "5678")

    @patch("apps.domains.students.services.import_students.send_parent_account_credentials_notice", return_value=False)
    def test_import_restore_notice_failure_rolls_back(self, _send_notice):
        student = self._deleted_with_unusable_parent()
        result = self._import(initial_password="unused-on-restore", parent_initial_password_mode="phone_last4")
        self.assertEqual(result["restored"], [])
        self.assertIn("복원을 취소", result["failed"][0]["error"])
        student.refresh_from_db()
        self.assertIsNotNone(student.deleted_at)
        self.deleted_parent_user.refresh_from_db()
        self.assertFalse(self.deleted_parent_user.has_usable_password())

    @patch("apps.domains.students.views.student_views.dispatch_job", return_value={"ok": True, "job_id": "bulk-choice-job"})
    @patch("apps.domains.students.views.student_views.upload_fileobj_to_r2_excel")
    def test_excel_upload_encrypts_both_fixed_values(self, _upload, dispatch):
        content = io.BytesIO()
        workbook = openpyxl.Workbook()
        workbook.save(content)
        response = self._request("bulk_create_from_excel", {
            "file": SimpleUploadedFile("students.xlsx", content.getvalue(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
            "initial_password_mode": "fixed", "initial_password": "  student  ",
            "parent_initial_password_mode": "fixed", "parent_initial_password": "  parent  ",
        }, format="multipart")
        self.assertEqual(response.status_code, 202, response.data)
        payload = dispatch.call_args.kwargs["payload"]
        self.assertNotIn("initial_password", payload)
        self.assertNotIn("parent_initial_password", payload)
        self.assertEqual(payload["password_mode"], "fixed")
        self.assertEqual(payload["parent_initial_password_mode"], "fixed")
        self.assertEqual(recover_excel_initial_password(payload), "  student  ")
        self.assertEqual(recover_excel_initial_password(payload, role="parent"), "  parent  ")

    def test_excel_worker_consumes_parent_secret_and_student_phone_choice(self):
        row = self.row

        class Storage:
            def download_to_path(self, bucket, key, local_path):
                workbook = openpyxl.Workbook()
                sheet = workbook.active
                sheet.append(["이름", "학부모전화번호", "학생전화번호"])
                sheet.append([row["name"], row["parent_phone"], row["phone"]])
                workbook.save(local_path)

        job = AIJobModel.objects.create(
            job_id="bulk-parent-worker", job_type="excel_parsing", status="RUNNING", tenant_id=str(self.tenant.id),
        )
        payload = {
            "file_key": "excel/synthetic.xlsx", "tenant_id": self.tenant.id,
            "password_mode": "phone_last4", "parent_initial_password_mode": "fixed",
            **protect_excel_initial_password("  worker-parent  ", role="parent"),
        }
        result = ExcelParsingService(Storage()).run(job.job_id, payload)
        self.assertEqual(result["created"], 1, result)
        student = self._student()
        self.assertTrue(student.user.check_password("1234"))
        self.assertTrue(student.parent.user.check_password("  worker-parent  "))
        job.refresh_from_db()
        self.assertEqual(job.status, "DONE")
