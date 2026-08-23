from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.core.models import Tenant
from apps.core.models.user import user_internal_username
from apps.domains.students.models import Student
from apps.domains.students.services import import_students_from_rows
from apps.domains.students.services.identity import derive_student_omr_code


User = get_user_model()


def _phone(block: int, index: int) -> str:
    return f"010{block:02d}{index:06d}"


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class StudentImportIdentityTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            name="학생 등록 식별 테스트",
            code="student-import-identity",
            is_active=True,
        )
        self.other_tenant = Tenant.objects.create(
            name="다른 학원",
            code="student-import-identity-other",
            is_active=True,
        )
        self.sequence = 0

    def _student(
        self,
        *,
        tenant: Tenant | None = None,
        name: str,
        phone: str | None,
        parent_phone: str,
        deleted: bool = False,
    ) -> Student:
        self.sequence += 1
        tenant = tenant or self.tenant
        ps_number = f"IDENTITY-{self.sequence:04d}"
        user = User.objects.create_user(
            username=user_internal_username(tenant, ps_number),
            password="original-password",
            tenant=tenant,
            phone=phone or "",
            name=name,
        )
        user.token_version = 7
        user.must_change_password = False
        user.save(update_fields=["token_version", "must_change_password"])
        return Student.objects.create(
            tenant=tenant,
            user=user,
            ps_number=ps_number,
            name=name,
            phone=phone,
            parent_phone=parent_phone,
            omr_code=derive_student_omr_code(phone=phone, parent_phone=parent_phone),
            uses_identifier=not bool(phone),
            school_type="HIGH",
            grade=1,
            deleted_at=timezone.now() if deleted else None,
            pending_account_notice_student_password_ciphertext="student-ciphertext",
            pending_account_notice_parent_password_ciphertext="parent-ciphertext",
            pending_account_notice_since=timezone.now(),
            pending_account_notice_origin_type="fixture",
            pending_account_notice_origin_id="fixture-job",
        )

    @staticmethod
    def _row(
        *,
        row: int,
        name: str,
        parent_phone: str,
        phone: str | None,
    ) -> dict:
        return {
            "_excel_row": row,
            "name": name,
            "parent_phone": parent_phone,
            "phone": phone or "",
            "school_type": "HIGH",
            "grade": 1,
        }

    def test_production_shaped_46_rows_resolve_to_10_created_36_duplicates(self):
        row_numbers = list(range(8, 54))
        phone_match_rows = {18, 22}
        name_parent_rows = [row for row in row_numbers if row not in phone_match_rows][:34]
        new_rows = [row for row in row_numbers if row not in phone_match_rows and row not in name_parent_rows]
        rows: list[dict] = []
        expected_duplicate_ids: dict[int, int] = {}
        invariant_students: list[Student] = []

        for row in name_parent_rows:
            name = f"명부표시{row}"
            parent_phone = _phone(20, row)
            existing_phone = None if row == 30 else _phone(21, row)
            existing = self._student(
                name=name,
                phone=existing_phone,
                parent_phone=parent_phone,
            )
            expected_duplicate_ids[row] = existing.id
            rows.append(
                self._row(
                    row=row,
                    name=name,
                    parent_phone=parent_phone,
                    phone=_phone(22, row),
                )
            )
            if row in (16, 30):
                invariant_students.append(existing)

        for row, suffix in ((18, "a"), (22, "2")):
            phone = _phone(23, row)
            existing = self._student(
                name=f"기존전화학생{row}",
                phone=phone,
                parent_phone=_phone(24, row),
            )
            expected_duplicate_ids[row] = existing.id
            invariant_students.append(existing)
            rows.append(
                self._row(
                    row=row,
                    name=f"명부표시학생{suffix}",
                    parent_phone=_phone(25, row),
                    phone=phone,
                )
            )

        for row in new_rows:
            rows.append(
                self._row(
                    row=row,
                    name=f"신규명부학생{row}",
                    parent_phone=_phone(26, row),
                    phone=_phone(27, row),
                )
            )

        before = {
            student.id: {
                "name": student.name,
                "phone": student.phone,
                "parent_phone": student.parent_phone,
                "ps_number": student.ps_number,
                "password": student.user.password,
                "token_version": student.user.token_version,
                "student_ciphertext": student.pending_account_notice_student_password_ciphertext,
                "parent_ciphertext": student.pending_account_notice_parent_password_ciphertext,
                "notice_origin_type": student.pending_account_notice_origin_type,
                "notice_origin_id": student.pending_account_notice_origin_id,
            }
            for student in invariant_students
        }

        result = import_students_from_rows(
            tenant_id=self.tenant.id,
            students_data=sorted(rows, key=lambda item: item["_excel_row"]),
            initial_password="new-import-password",
            source_job_id="pii-free-production-shaped-job",
        )

        self.assertEqual(result["created"], 10)
        self.assertEqual(len(result["duplicates"]), 36)
        self.assertEqual(result["failed"], [])
        self.assertEqual(result["restored"], [])
        duplicate_by_row = {item["row"]: item["student_id"] for item in result["duplicates"]}
        self.assertEqual(duplicate_by_row, expected_duplicate_ids)
        self.assertEqual(
            Student.objects.filter(tenant=self.tenant, deleted_at__isnull=True).count(),
            46,
        )

        for student in invariant_students:
            student.refresh_from_db()
            student.user.refresh_from_db()
            self.assertEqual(
                {
                    "name": student.name,
                    "phone": student.phone,
                    "parent_phone": student.parent_phone,
                    "ps_number": student.ps_number,
                    "password": student.user.password,
                    "token_version": student.user.token_version,
                    "student_ciphertext": student.pending_account_notice_student_password_ciphertext,
                    "parent_ciphertext": student.pending_account_notice_parent_password_ciphertext,
                    "notice_origin_type": student.pending_account_notice_origin_type,
                    "notice_origin_id": student.pending_account_notice_origin_id,
                },
                before[student.id],
            )

    def test_phone_match_wins_without_mutating_a_distinct_name_parent_candidate(self):
        by_phone = self._student(
            name="전화번호기준학생",
            phone="01031000001",
            parent_phone="01032000001",
        )
        by_name_parent = self._student(
            name="명부표시학생a",
            phone="01031000002",
            parent_phone="01032000002",
        )

        result = import_students_from_rows(
            tenant_id=self.tenant.id,
            students_data=[
                self._row(
                    row=18,
                    name=by_name_parent.name,
                    parent_phone=by_name_parent.parent_phone,
                    phone=by_phone.phone,
                )
            ],
            initial_password="new-password",
        )

        self.assertEqual(result["created"], 0)
        self.assertEqual(result["failed"], [])
        self.assertEqual(result["duplicates"][0]["student_id"], by_phone.id)
        by_phone.refresh_from_db()
        by_name_parent.refresh_from_db()
        self.assertEqual(by_phone.name, "전화번호기준학생")
        self.assertEqual(by_phone.parent_phone, "01032000001")
        self.assertEqual(by_name_parent.phone, "01031000002")

    def test_opaque_suffix_names_and_shared_parent_phone_create_distinct_students(self):
        parent_phone = "01033000001"
        names = ["김지우a", "김지우b", "김지우1", "김지우2"]
        rows = [
            self._row(
                row=index + 8,
                name=name,
                parent_phone=parent_phone,
                phone=_phone(34, index),
            )
            for index, name in enumerate(names, start=1)
        ]

        result = import_students_from_rows(
            tenant_id=self.tenant.id,
            students_data=rows,
            initial_password="new-password",
        )

        self.assertEqual(result["created"], 4)
        self.assertEqual(result["duplicates"], [])
        self.assertEqual(result["failed"], [])
        self.assertEqual(
            set(Student.objects.filter(tenant=self.tenant).values_list("name", flat=True)),
            set(names),
        )
        self.assertEqual(
            Student.objects.filter(tenant=self.tenant, parent_phone=parent_phone).count(),
            4,
        )

    def test_exact_name_match_does_not_fold_suffix_case(self):
        self._student(
            name="김지우a",
            phone=None,
            parent_phone="01035000001",
        )

        result = import_students_from_rows(
            tenant_id=self.tenant.id,
            students_data=[
                self._row(
                    row=8,
                    name="김지우A",
                    parent_phone="01035000001",
                    phone=None,
                )
            ],
            initial_password="new-password",
        )

        self.assertEqual(result["created"], 1)
        self.assertEqual(result["duplicates"], [])
        self.assertEqual(
            set(Student.objects.filter(tenant=self.tenant).values_list("name", flat=True)),
            {"김지우a", "김지우A"},
        )

    def test_active_phone_ambiguity_fails_closed(self):
        shared_phone = "01036000001"
        self._student(name="전화중복1", phone=shared_phone, parent_phone="01036100001")
        self._student(name="전화중복2", phone=shared_phone, parent_phone="01036100002")

        result = import_students_from_rows(
            tenant_id=self.tenant.id,
            students_data=[
                self._row(
                    row=18,
                    name="전화중복명부",
                    parent_phone="01036100003",
                    phone=shared_phone,
                )
            ],
            initial_password="new-password",
        )

        self.assertEqual(result["created"], 0)
        self.assertEqual(result["duplicates"], [])
        self.assertEqual(result["failed"][0]["row"], 18)
        self.assertIn("학생 전화번호가 같은 활성 학생이 여러 명", result["failed"][0]["error"])
        self.assertEqual(Student.objects.filter(tenant=self.tenant).count(), 2)

    def test_exact_name_parent_ambiguity_fails_closed(self):
        for index in (1, 2):
            self._student(
                name="동일표시학생",
                phone=_phone(37, index),
                parent_phone="01037100001",
            )

        result = import_students_from_rows(
            tenant_id=self.tenant.id,
            students_data=[
                self._row(
                    row=30,
                    name="동일표시학생",
                    parent_phone="01037100001",
                    phone=None,
                )
            ],
            initial_password="new-password",
        )

        self.assertEqual(result["created"], 0)
        self.assertEqual(result["duplicates"], [])
        self.assertIn("이름과 학부모 전화번호가 같은 활성 학생이 여러 명", result["failed"][0]["error"])
        self.assertEqual(Student.objects.filter(tenant=self.tenant).count(), 2)

    def test_deleted_name_parent_ambiguity_fails_closed(self):
        for index in (1, 2):
            self._student(
                name="삭제중복학생",
                phone=_phone(38, index),
                parent_phone="01038100001",
                deleted=True,
            )

        result = import_students_from_rows(
            tenant_id=self.tenant.id,
            students_data=[
                self._row(
                    row=31,
                    name="삭제중복학생",
                    parent_phone="01038100001",
                    phone=None,
                )
            ],
            initial_password="new-password",
        )

        self.assertEqual(result["created"], 0)
        self.assertEqual(result["restored"], [])
        self.assertIn("이름과 학부모 전화번호가 같은 삭제 학생이 여러 명", result["failed"][0]["error"])
        self.assertEqual(
            Student.objects.filter(tenant=self.tenant, deleted_at__isnull=False).count(),
            2,
        )

    def test_deleted_phone_conflict_fails_and_cross_tenant_match_does_not_resolve(self):
        deleted_phone = "01039000001"
        deleted = self._student(
            name="삭제전화학생",
            phone=deleted_phone,
            parent_phone="01039100001",
            deleted=True,
        )
        other_phone = "01039000002"
        self._student(
            tenant=self.other_tenant,
            name="타학원학생",
            phone=other_phone,
            parent_phone="01039100002",
        )

        result = import_students_from_rows(
            tenant_id=self.tenant.id,
            students_data=[
                self._row(
                    row=32,
                    name="삭제전화다른이름",
                    parent_phone="01039100003",
                    phone=deleted_phone,
                ),
                self._row(
                    row=33,
                    name="타학원학생",
                    parent_phone="01039100002",
                    phone=other_phone,
                ),
            ],
            initial_password="new-password",
        )

        self.assertEqual(result["created"], 1)
        self.assertEqual(len(result["failed"]), 1)
        self.assertEqual(result["failed"][0]["conflict_student_id"], deleted.id)
        self.assertEqual(
            Student.objects.filter(tenant=self.tenant, phone=other_phone, deleted_at__isnull=True).count(),
            1,
        )
        self.assertEqual(
            Student.objects.filter(tenant=self.other_tenant, phone=other_phone, deleted_at__isnull=True).count(),
            1,
        )
