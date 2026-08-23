from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import openpyxl
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password
from django.test import TestCase
from django.utils import timezone

from academy.adapters.db.django.repositories_ai import job_create
from academy.application.services.excel_parsing_service import ExcelParsingService
from apps.core.models import PendingPasswordReset, Tenant
from apps.domains.enrollment.models import Enrollment
from apps.domains.enrollment.services import lecture_enroll_from_excel_rows
from apps.domains.lectures.test_support import create_lecture_fixture
from apps.domains.students.test_support import create_student_fixture
from apps.support.enrollment.import_dependencies import active_student_for_import_identity


User = get_user_model()


class _WorkbookStorage:
    def __init__(self, rows: list[list[str]]) -> None:
        self.rows = rows

    def download_to_path(self, bucket: str, key: str, local_path: str) -> None:
        workbook = openpyxl.Workbook()
        worksheet = workbook.active
        for row in self.rows:
            worksheet.append(row)
        workbook.save(local_path)


class ExistingStudentExcelEnrollmentTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            name="기존 학생 수강 엑셀 학원",
            code="existing_student_excel_enrollment",
            is_active=True,
        )
        self.lecture = create_lecture_fixture(
            tenant=self.tenant,
            title="기존 학생 수강 강의",
            name="기존 학생 수강 강의",
            subject="MATH",
        )
        self.user = User.objects.create_user(
            username="existing-excel-student",
            password="keep-current-password",
            tenant=self.tenant,
            token_version=7,
        )
        self.student = create_student_fixture(
            tenant=self.tenant,
            user=self.user,
            ps_number="EXISTING-EXCEL-001",
            omr_code="87654321",
            name="기존학생",
            phone="01090001234",
            parent_phone="01087654321",
            school_type="HIGH",
        )
        self.student.pending_account_notice_student_password_ciphertext = "student-ciphertext"
        self.student.pending_account_notice_parent_password_ciphertext = "parent-ciphertext"
        self.student.pending_account_notice_origin_type = "excel_import"
        self.student.pending_account_notice_origin_id = "student-registration-job"
        self.student.pending_account_notice_since = timezone.now()
        self.student.save(
            update_fields=[
                "pending_account_notice_student_password_ciphertext",
                "pending_account_notice_parent_password_ciphertext",
                "pending_account_notice_origin_type",
                "pending_account_notice_origin_id",
                "pending_account_notice_since",
            ]
        )
        self.pending_reset = PendingPasswordReset.objects.create(
            tenant=self.tenant,
            user=self.user,
            password_hash=make_password("pending-password"),
            expires_at=timezone.now() + timedelta(minutes=10),
        )

    @patch("apps.domains.enrollment.services.schedule_pending_account_notice")
    def test_existing_only_import_preserves_credentials_and_skips_unknown_student(
        self,
        schedule_notice,
    ):
        password_hash = self.user.password
        pending_reset_hash = self.pending_reset.password_hash

        result = lecture_enroll_from_excel_rows(
            tenant_id=self.tenant.id,
            lecture_id=self.lecture.id,
            students_data=[
                {
                    "name": self.student.name,
                    "parent_phone": self.student.parent_phone,
                    "phone": self.student.phone,
                },
                {
                    "name": "명부에없는학생",
                    "parent_phone": "01070009999",
                    "phone": "01090009999",
                },
            ],
            initial_password="must-never-replace-existing-password",
            password_mode="fixed",
        )

        self.user.refresh_from_db()
        self.student.refresh_from_db()
        self.pending_reset.refresh_from_db()
        self.assertEqual(self.user.username, "existing-excel-student")
        self.assertEqual(self.user.password, password_hash)
        self.assertTrue(self.user.check_password("keep-current-password"))
        self.assertEqual(self.user.token_version, 7)
        self.assertEqual(self.student.ps_number, "EXISTING-EXCEL-001")
        self.assertEqual(
            self.student.pending_account_notice_student_password_ciphertext,
            "student-ciphertext",
        )
        self.assertEqual(
            self.student.pending_account_notice_parent_password_ciphertext,
            "parent-ciphertext",
        )
        self.assertEqual(self.student.pending_account_notice_origin_type, "excel_import")
        self.assertEqual(self.student.pending_account_notice_origin_id, "student-registration-job")
        self.assertEqual(self.pending_reset.password_hash, pending_reset_hash)
        self.assertEqual(result["created_students_count"], 0)
        self.assertEqual(result["enrolled_count"], 1)
        self.assertEqual(result["not_found_students_count"], 1)
        self.assertEqual(result["ambiguous_students_count"], 0)
        self.assertTrue(
            Enrollment.objects.filter(
                tenant=self.tenant,
                lecture=self.lecture,
                student=self.student,
                status="ACTIVE",
            ).exists()
        )
        self.assertIsNone(
            active_student_for_import_identity(
                self.tenant,
                name="명부에없는학생",
                parent_phone="01070009999",
            )
        )
        schedule_notice.assert_called_once_with(student_id=self.student.id)

    @patch("apps.domains.enrollment.services.schedule_pending_account_notice")
    def test_duplicate_rows_and_retry_keep_one_enrollment(self, schedule_notice):
        row = {
            "name": self.student.name,
            "parent_phone": self.student.parent_phone,
            "phone": self.student.phone,
        }

        first = lecture_enroll_from_excel_rows(
            tenant_id=self.tenant.id,
            lecture_id=self.lecture.id,
            students_data=[row, dict(row)],
        )
        retry = lecture_enroll_from_excel_rows(
            tenant_id=self.tenant.id,
            lecture_id=self.lecture.id,
            students_data=[row, dict(row)],
        )

        self.assertEqual(first["enrolled_count"], 1)
        self.assertEqual(retry["enrolled_count"], 1)
        self.assertEqual(
            Enrollment.objects.filter(
                tenant=self.tenant,
                lecture=self.lecture,
                student=self.student,
            ).count(),
            1,
        )
        self.assertEqual(schedule_notice.call_count, 2)

    @patch("apps.domains.enrollment.services.schedule_pending_account_notice")
    def test_same_student_repeated_by_ps_number_and_fallback_is_processed_once(
        self,
        schedule_notice,
    ):
        result = lecture_enroll_from_excel_rows(
            tenant_id=self.tenant.id,
            lecture_id=self.lecture.id,
            students_data=[
                {
                    "ps_number": self.student.ps_number,
                    "name": self.student.name,
                    "parent_phone": self.student.parent_phone,
                },
                {
                    "name": self.student.name,
                    "parent_phone": self.student.parent_phone,
                },
            ],
        )

        self.assertEqual(result["enrolled_count"], 1)
        self.assertEqual(
            Enrollment.objects.filter(
                tenant=self.tenant,
                lecture=self.lecture,
                student=self.student,
            ).count(),
            1,
        )
        schedule_notice.assert_called_once_with(student_id=self.student.id)

    @patch("apps.domains.enrollment.services.schedule_pending_account_notice")
    def test_ps_number_takes_priority_over_name_parent_and_blank_student_phone(
        self,
        schedule_notice,
    ):
        self.student.name = "김지우a"
        self.student.save(update_fields=["name"])
        sibling_user = User.objects.create_user(
            username="kim-jiwoo-b",
            password="keep-sibling-password",
            tenant=self.tenant,
        )
        sibling = create_student_fixture(
            tenant=self.tenant,
            user=sibling_user,
            ps_number="KIM-JIWOO-B",
            omr_code="87654325",
            name="김지우b",
            phone=None,
            parent_phone=self.student.parent_phone,
            school_type="HIGH",
        )

        result = lecture_enroll_from_excel_rows(
            tenant_id=self.tenant.id,
            lecture_id=self.lecture.id,
            students_data=[{
                "ps_number": sibling.ps_number,
                "name": self.student.name,
                "parent_phone": self.student.parent_phone,
                "phone": "",
            }],
        )

        self.assertEqual(result["enrolled_count"], 1)
        self.assertTrue(
            Enrollment.objects.filter(
                tenant=self.tenant,
                lecture=self.lecture,
                student=sibling,
            ).exists()
        )
        self.assertFalse(
            Enrollment.objects.filter(
                tenant=self.tenant,
                lecture=self.lecture,
                student=self.student,
            ).exists()
        )
        schedule_notice.assert_called_once_with(student_id=sibling.id)

    @patch("apps.domains.enrollment.services.schedule_pending_account_notice")
    def test_exact_suffix_name_distinguishes_siblings_with_same_parent_phone(
        self,
        schedule_notice,
    ):
        self.student.name = "김지우1"
        self.student.phone = None
        self.student.save(update_fields=["name", "phone"])
        sibling_user = User.objects.create_user(
            username="kim-jiwoo-2",
            password="keep-sibling-password",
            tenant=self.tenant,
        )
        sibling = create_student_fixture(
            tenant=self.tenant,
            user=sibling_user,
            ps_number="KIM-JIWOO-2",
            omr_code="87654326",
            name="김지우2",
            phone=None,
            parent_phone=self.student.parent_phone,
            school_type="HIGH",
        )

        result = lecture_enroll_from_excel_rows(
            tenant_id=self.tenant.id,
            lecture_id=self.lecture.id,
            students_data=[{
                "name": sibling.name,
                "parent_phone": sibling.parent_phone,
                "phone": "",
            }],
        )

        self.assertEqual(result["enrolled_count"], 1)
        self.assertEqual(result["ambiguous_students_count"], 0)
        self.assertTrue(
            Enrollment.objects.filter(
                tenant=self.tenant,
                lecture=self.lecture,
                student=sibling,
            ).exists()
        )
        self.assertFalse(
            Enrollment.objects.filter(
                tenant=self.tenant,
                lecture=self.lecture,
                student=self.student,
            ).exists()
        )
        schedule_notice.assert_called_once_with(student_id=sibling.id)

    def test_unknown_ps_number_does_not_fallback_to_name_parent(self):
        with self.assertRaisesRegex(
            ValueError,
            "학생 명부에서 등록할 수 있는 학생을 찾지 못했습니다",
        ):
            lecture_enroll_from_excel_rows(
                tenant_id=self.tenant.id,
                lecture_id=self.lecture.id,
                students_data=[{
                    "ps_number": "UNKNOWN-PS-NUMBER",
                    "name": self.student.name,
                    "parent_phone": self.student.parent_phone,
                    "phone": "",
                }],
            )

        self.assertFalse(
            Enrollment.objects.filter(
                tenant=self.tenant,
                lecture=self.lecture,
            ).exists()
        )

    @patch("apps.domains.enrollment.services.schedule_pending_account_notice")
    def test_ambiguous_identity_is_skipped_while_unique_student_enrolls(
        self,
        schedule_notice,
    ):
        duplicate_user = User.objects.create_user(
            username="duplicate-existing-excel-student",
            password="keep-duplicate-password",
            tenant=self.tenant,
        )
        duplicate_student = create_student_fixture(
            tenant=self.tenant,
            user=duplicate_user,
            ps_number="EXISTING-EXCEL-DUPLICATE",
            omr_code="87654322",
            name=self.student.name,
            parent_phone=self.student.parent_phone,
            school_type="HIGH",
        )
        unique_user = User.objects.create_user(
            username="unique-existing-excel-student",
            password="keep-unique-password",
            tenant=self.tenant,
        )
        unique_student = create_student_fixture(
            tenant=self.tenant,
            user=unique_user,
            ps_number="EXISTING-EXCEL-UNIQUE",
            omr_code="87654323",
            name="고유학생",
            parent_phone="01081112222",
            school_type="HIGH",
        )

        result = lecture_enroll_from_excel_rows(
            tenant_id=self.tenant.id,
            lecture_id=self.lecture.id,
            students_data=[
                {"name": self.student.name, "parent_phone": self.student.parent_phone},
                {"name": unique_student.name, "parent_phone": unique_student.parent_phone},
            ],
        )

        self.assertEqual(result["enrolled_count"], 1)
        self.assertEqual(result["not_found_students_count"], 0)
        self.assertEqual(result["ambiguous_students_count"], 1)
        self.assertFalse(
            Enrollment.objects.filter(
                tenant=self.tenant,
                lecture=self.lecture,
                student__in=[self.student, duplicate_student],
            ).exists()
        )
        self.assertTrue(
            Enrollment.objects.filter(
                tenant=self.tenant,
                lecture=self.lecture,
                student=unique_student,
                status="ACTIVE",
            ).exists()
        )
        schedule_notice.assert_called_once_with(student_id=unique_student.id)

    def test_ambiguous_identity_only_fails_without_creating_enrollment(self):
        duplicate_user = User.objects.create_user(
            username="duplicate-only-existing-excel-student",
            password="keep-duplicate-password",
            tenant=self.tenant,
        )
        create_student_fixture(
            tenant=self.tenant,
            user=duplicate_user,
            ps_number="EXISTING-EXCEL-DUPLICATE-ONLY",
            omr_code="87654324",
            name=self.student.name,
            parent_phone=self.student.parent_phone,
            school_type="HIGH",
        )

        with self.assertRaisesRegex(ValueError, "등록 대상을 확정할 수 없습니다"):
            lecture_enroll_from_excel_rows(
                tenant_id=self.tenant.id,
                lecture_id=self.lecture.id,
                students_data=[
                    {"name": self.student.name, "parent_phone": self.student.parent_phone},
                ],
            )

        self.assertFalse(
            Enrollment.objects.filter(
                tenant=self.tenant,
                lecture=self.lecture,
            ).exists()
        )

    def test_deleted_student_is_not_enrolled(self):
        self.student.deleted_at = timezone.now()
        self.student.save(update_fields=["deleted_at"])

        with self.assertRaisesRegex(
            ValueError,
            "학생 명부에서 등록할 수 있는 학생을 찾지 못했습니다",
        ):
            lecture_enroll_from_excel_rows(
                tenant_id=self.tenant.id,
                lecture_id=self.lecture.id,
                students_data=[
                    {"name": self.student.name, "parent_phone": self.student.parent_phone},
                ],
            )

        self.assertFalse(
            Enrollment.objects.filter(
                tenant=self.tenant,
                lecture=self.lecture,
            ).exists()
        )

    def test_same_identity_in_another_tenant_is_not_enrolled_or_created(self):
        other_tenant = Tenant.objects.create(
            name="다른 학원",
            code="other_existing_student_excel_enrollment",
            is_active=True,
        )
        other_user = User.objects.create_user(
            username="other-tenant-student",
            password="other-password",
            tenant=other_tenant,
        )
        create_student_fixture(
            tenant=other_tenant,
            user=other_user,
            ps_number="OTHER-001",
            omr_code="11223344",
            name="다른학원학생",
            parent_phone="01011223344",
            school_type="HIGH",
        )

        with self.assertRaisesRegex(
            ValueError,
            "학생 명부에서 등록할 수 있는 학생을 찾지 못했습니다",
        ):
            lecture_enroll_from_excel_rows(
                tenant_id=self.tenant.id,
                lecture_id=self.lecture.id,
                students_data=[
                    {
                        "name": "다른학원학생",
                        "parent_phone": "01011223344",
                    }
                ],
                initial_password="must-not-create-account",
                password_mode="fixed",
            )

        self.assertIsNone(
            active_student_for_import_identity(
                self.tenant,
                name="다른학원학생",
                parent_phone="01011223344",
            )
        )
        self.assertFalse(
            Enrollment.objects.filter(
                tenant=self.tenant,
                lecture=self.lecture,
            ).exists()
        )

    @patch(
        "apps.domains.ai.services.excel_job_secrets.recover_excel_initial_password",
        side_effect=AssertionError("enrollment worker must not recover a password"),
    )
    def test_enrollment_worker_does_not_read_or_require_password_secret(
        self,
        _recover_password,
    ):
        job = job_create(
            job_id="existing-student-enrollment-job",
            job_type="excel_parsing",
            status="RUNNING",
            tenant_id=str(self.tenant.id),
            payload={},
        )

        result = ExcelParsingService(
            _WorkbookStorage([
                ["학생번호", "이름", "학부모전화번호", "학생전화번호"],
                [
                    self.student.ps_number,
                    "표시이름이달라도학생번호우선",
                    self.student.parent_phone,
                    "",
                ],
            ])
        ).run(
            job.job_id,
            {
                "file_key": "excel/existing-students.xlsx",
                "bucket": "academy-excel",
                "tenant_id": self.tenant.id,
                "lecture_id": self.lecture.id,
                "student_match_mode": "existing_only",
            },
        )

        self.assertEqual(result["enrolled_count"], 1)
        self.assertEqual(result["created_students_count"], 0)
        self.assertNotIn("credentials", result)
        _recover_password.assert_not_called()
