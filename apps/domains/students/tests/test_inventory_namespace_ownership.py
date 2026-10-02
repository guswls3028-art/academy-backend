from django.test import TestCase

from apps.domains.inventory.models import InventoryFile, InventoryFolder
from apps.domains.students.services import restore_student, soft_delete_student
from apps.domains.students.tests.test_identity_lifecycle import _create_student, _create_tenant


class StudentInventoryNamespaceOwnershipTests(TestCase):
    def setUp(self):
        self.tenant = _create_tenant(code="inventory-owner-regression")
        self.student = _create_student(self.tenant, "REUSABLE", parent_phone="")
        self.folder = InventoryFolder.objects.create(
            tenant=self.tenant, scope="student", student_ps="REUSABLE", name="내 자료",
        )
        self.file = InventoryFile.objects.create(
            tenant=self.tenant, scope="student", student_ps="REUSABLE", folder=self.folder,
            display_name="수동 자료", original_name="original.pdf", r2_key="qa/owner-original.pdf",
        )

    def test_soft_delete_preserves_original_files_outside_reused_number(self):
        soft_delete_student(self.student, tenant=self.tenant)
        self.student.refresh_from_db()
        self.folder.refresh_from_db()
        self.file.refresh_from_db()
        self.assertEqual(self.folder.student_ps, self.student.ps_number)
        self.assertEqual(self.file.student_ps, self.student.ps_number)
        replacement = _create_student(
            self.tenant, "REUSABLE", phone="01022223333", parent_phone="",
        )
        self.assertFalse(InventoryFile.objects.filter(
            tenant=self.tenant, scope="student", student_ps=replacement.ps_number,
        ).exists())
        self.assertEqual(self.file.r2_key, "qa/owner-original.pdf")

    def test_restore_keeps_each_students_files_after_number_reuse(self):
        soft_delete_student(self.student, tenant=self.tenant)
        replacement = _create_student(
            self.tenant, "REUSABLE", phone="01022223333", parent_phone="",
        )
        replacement_file = InventoryFile.objects.create(
            tenant=self.tenant, scope="student", student_ps="REUSABLE",
            display_name="새 학생 자료", original_name="replacement.pdf", r2_key="qa/replacement.pdf",
        )
        replacement.ps_number = "REPLACEMENT"
        replacement.save(update_fields=["ps_number"])
        restore_student(self.student, tenant=self.tenant)
        self.file.refresh_from_db()
        self.folder.refresh_from_db()
        replacement_file.refresh_from_db()
        self.assertEqual(self.file.student_ps, "REUSABLE")
        self.assertEqual(self.folder.student_ps, "REUSABLE")
        self.assertEqual(replacement_file.student_ps, "REPLACEMENT")
        self.assertEqual(self.file.r2_key, "qa/owner-original.pdf")

    def test_same_number_in_other_tenant_is_preserved(self):
        other_tenant = _create_tenant(code="inventory-owner-other")
        other_file = InventoryFile.objects.create(
            tenant=other_tenant, scope="student", student_ps="REUSABLE",
            display_name="다른 학원 자료", original_name="other.pdf", r2_key="qa/other-tenant.pdf",
        )
        soft_delete_student(self.student, tenant=self.tenant)
        restore_student(self.student, tenant=self.tenant)
        other_file.refresh_from_db()
        self.assertEqual(other_file.student_ps, "REUSABLE")
        self.assertEqual(other_file.r2_key, "qa/other-tenant.pdf")
