from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIRequestFactory, force_authenticate
from openpyxl import Workbook

from academy.application.services.excel_parsing_service import parse_student_excel_file

from apps.core.models import Tenant
from apps.core.models.tenant_membership import TenantMembership
from apps.core.models.user import user_internal_username
from apps.domains.students.models import Student
from apps.domains.students.services import import_students_from_rows
from apps.domains.students.views import StudentViewSet


User = get_user_model()


def _tenant(*, name: str, code: str) -> Tenant:
    return Tenant.objects.create(account_password_policy={"parent_mode": "phone_last4"}, name=name, code=code, is_active=True)


def _staff(*, tenant: Tenant, username: str):
    user = User.objects.create_user(
        username=username,
        password="test-password",
        tenant=tenant,
        is_staff=True,
        name=f"Staff-{username}",
    )
    TenantMembership.ensure_active(tenant=tenant, user=user, role="owner")
    return user


def _student(
    *,
    tenant: Tenant,
    ps_number: str,
    name: str,
    phone: str,
    parent_phone: str,
) -> Student:
    user = User.objects.create_user(
        username=user_internal_username(tenant, ps_number),
        password="test-password",
        tenant=tenant,
        phone=phone,
        name=name,
    )
    TenantMembership.ensure_active(tenant=tenant, user=user, role="student")
    return Student.objects.create(
        tenant=tenant,
        user=user,
        ps_number=ps_number,
        name=name,
        phone=phone,
        parent_phone=parent_phone,
        omr_code=phone.replace("-", "")[-8:],
    )


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class StudentImportResultContractTests(TestCase):
    def setUp(self):
        self.tenant = _tenant(name="Import Result Academy", code="import-result")

    def test_fractional_boolean_and_infinite_grades_fail_without_account_creation(self):
        for grade in (1.5, True, float("inf"), float("nan")):
            with self.subTest(grade=grade):
                result = import_students_from_rows(
                    tenant_id=self.tenant.id,
                    students_data=[{
                        "name": "학년오류학생", "parent_phone": "01070000001",
                        "phone": "01080000001", "grade": grade,
                    }],
                    initial_password="test-password",
                )
                self.assertEqual(result["created"], 0)
                self.assertEqual(result["failed"][0]["reason_code"], "invalid_row")
                self.assertIn("정수", result["failed"][0]["error"])
                self.assertFalse(Student.objects.filter(tenant=self.tenant).exists())
                self.assertFalse(User.objects.filter(tenant=self.tenant).exists())

    def test_json_import_rejects_contact_coercion_before_any_account_is_created(self):
        for field in ("phone", "studentPhone", "parent_phone", "parentPhone"):
            for value in (0, False, "번호오류", "abc01080000001", "010８００００００１"):
                with self.subTest(field=field, value=value):
                    row = {"name": "연락처오류학생", "parent_phone": "01070000001"}
                    if field == "parentPhone":
                        row.pop("parent_phone")
                    row[field] = value
                    result = import_students_from_rows(
                        tenant_id=self.tenant.id, students_data=[row],
                        initial_password="test-password",
                    )
                    self.assertEqual(result["created"], 0)
                    self.assertEqual(len(result["failed"]), 1)
                    self.assertFalse(User.objects.filter(tenant=self.tenant).exists())

    def test_real_workbook_import_retry_preserves_sibling_accounts_and_tenant_isolation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "siblings.xlsx"
            workbook = Workbook()
            sheet = workbook.active
            sheet.append(["이름", "학부모전화번호", "학생전화번호", "학교유형", "학년"])
            sheet.append(["첫째학생", 1070000001, 1080000001, "MIDDLE", 2])
            sheet.append(["둘째학생", 1070000001, "", "MIDDLE", 1])
            sheet.append(["오류학생", 1070000001, "0101234567", "MIDDLE", 1])
            workbook.save(path)
            errors = []
            rows, _ = parse_student_excel_file(str(path), validation_errors_out=errors)

        result = import_students_from_rows(
            tenant_id=self.tenant.id, students_data=rows, initial_password="chosen-student",
        )
        self.assertEqual(result["created"], 2)
        self.assertEqual(result["failed"], [])
        self.assertEqual([error["row"] for error in errors], [4])
        students = list(Student.objects.filter(tenant=self.tenant).select_related("parent__user", "user").order_by("id"))
        self.assertEqual(students[0].school_type, "MIDDLE")
        self.assertEqual(students[0].phone, "01080000001")
        self.assertFalse(students[1].phone)
        self.assertEqual(students[0].parent_id, students[1].parent_id)
        self.assertNotEqual(students[0].user_id, students[1].user_id)
        parent_user = students[0].parent.user
        parent_user.set_password("existing-parent-password")
        parent_user.save(update_fields=["password"])
        parent_hash = parent_user.password

        retried = import_students_from_rows(
            tenant_id=self.tenant.id, students_data=rows, initial_password="different-password",
        )
        self.assertEqual(retried["created"], 0)
        self.assertEqual(len(retried["duplicates"]), 2)
        parent_user.refresh_from_db()
        self.assertEqual(parent_user.password, parent_hash)
        for student in students:
            student.user.refresh_from_db()
            self.assertTrue(student.user.check_password("chosen-student"))
            self.assertTrue(TenantMembership.objects.filter(tenant=self.tenant, user=student.user, is_active=True).exists())

        other = _tenant(name="Separate Import", code="separate-import")
        other_result = import_students_from_rows(
            tenant_id=other.id, students_data=rows, initial_password="other-password",
        )
        self.assertEqual(other_result["created"], 2)
        self.assertNotEqual(Student.objects.filter(tenant=other).first().parent_id, students[0].parent_id)

    def test_created_rows_preserve_excel_row_name_and_created_student_id(self):
        result = import_students_from_rows(
            tenant_id=self.tenant.id,
            students_data=[{
                "_excel_row": 7,
                "name": "합성학생A",
                "parent_phone": "01070000001",
                "phone": "01080000001",
                "school_type": "HIGH",
                "grade": 1,
            }],
            initial_password="test-password",
        )

        created = Student.objects.get(tenant=self.tenant, name="합성학생A")
        self.assertEqual(result["created"], 1)
        self.assertEqual(result["created_rows"], [{
            "row": 7,
            "name": "합성학생A",
            "student_id": created.id,
        }])

    @patch(
        "apps.domains.students.services.import_students.resolve_student_import_row",
        side_effect=RuntimeError("internal-db-detail-must-not-leak"),
    )
    def test_unexpected_row_exception_returns_safe_reason(self, _resolve_mock):
        result = import_students_from_rows(
            tenant_id=self.tenant.id,
            students_data=[{
                "_excel_row": 11,
                "name": "합성학생B",
                "parent_phone": "01070000002",
                "phone": "01080000002",
            }],
            initial_password="test-password",
        )

        self.assertEqual(result["failed"], [{
            "row": 11,
            "name": "합성학생B",
            "error": "처리 중 오류가 발생했습니다. 입력값을 확인한 뒤 다시 시도해 주세요.",
            "reason_code": "processing_error",
            "conflict_student_id": None,
        }])
        self.assertNotIn("internal-db-detail", str(result))


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class StudentPhoneSearchNormalizationTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = _tenant(name="Phone Search Academy", code="phone-search")
        self.other_tenant = _tenant(name="Other Academy", code="phone-search-other")
        self.admin = _staff(tenant=self.tenant, username="phone-search-admin")
        self.student_digits = _student(
            tenant=self.tenant,
            ps_number="PHONE-001",
            name="합성학생C",
            phone="01088424864",
            parent_phone="01071112222",
        )
        self.student_hyphens = _student(
            tenant=self.tenant,
            ps_number="PHONE-002",
            name="합성학생D",
            phone="010-9555-6666",
            parent_phone="010-7222-3333",
        )
        _student(
            tenant=self.other_tenant,
            ps_number="PHONE-003",
            name="타학원합성학생",
            phone="01088424864",
            parent_phone="01073334444",
        )

    def _list(self, params: dict[str, str]):
        request = self.factory.get("/api/v1/students/", params)
        force_authenticate(request, user=self.admin)
        request.tenant = self.tenant
        return StudentViewSet.as_view({"get": "list"})(request)

    @staticmethod
    def _ids(response) -> list[int]:
        return [row["id"] for row in response.data.get("results", response.data)]

    def test_general_search_matches_full_phone_with_or_without_hyphens(self):
        formatted = self._list({"search": "010-8842-4864"})
        digits = self._list({"search": "01095556666"})

        self.assertEqual(formatted.status_code, 200)
        self.assertEqual(self._ids(formatted), [self.student_digits.id])
        self.assertEqual(self._ids(digits), [self.student_hyphens.id])

    def test_phone_search_is_exact_and_tenant_scoped(self):
        response = self._list({"search": "010-8842-4864"})
        near_match = self._list({"search": "010-8842-4865"})

        self.assertEqual(self._ids(response), [self.student_digits.id])
        self.assertEqual(self._ids(near_match), [])

    def test_phone_filters_share_normalized_exact_match_contract(self):
        student_phone = self._list({"student_phone": "010-9555-6666"})
        parent_phone = self._list({"parent_phone": "01072223333"})

        self.assertEqual(self._ids(student_phone), [self.student_hyphens.id])
        self.assertEqual(self._ids(parent_phone), [self.student_hyphens.id])

    def test_non_phone_text_search_keeps_existing_name_behavior(self):
        response = self._list({"search": "합성학생C"})

        self.assertEqual(self._ids(response), [self.student_digits.id])
