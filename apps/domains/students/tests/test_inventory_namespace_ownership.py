from django.apps import apps
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.domains.students.services import restore_student, soft_delete_student
from apps.domains.students.services.identity import student_login_id_taken
from apps.domains.students.tests.test_identity_lifecycle import _create_student, _create_tenant
from apps.domains.students.tests.test_student_profile_canonicalization import make_admin
from apps.domains.students.views import StudentViewSet


InventoryFile = apps.get_model("inventory", "InventoryFile")
InventoryFolder = apps.get_model("inventory", "InventoryFolder")

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

    def _patch_profile(self, payload):
        request = APIRequestFactory().patch(
            f"/api/v1/students/{self.student.pk}/", payload, format="json",
        )
        request.tenant = self.tenant
        force_authenticate(request, user=make_admin(self.tenant))
        return StudentViewSet.as_view({"patch": "partial_update"})(request, pk=self.student.pk)

    def test_profile_update_preserves_own_existing_files(self):
        response = self._patch_profile({"high_school": "Updated High"})
        self.assertEqual(response.status_code, 200)
        self.student.refresh_from_db()
        self.folder.refresh_from_db()
        self.file.refresh_from_db()
        self.assertEqual(self.student.high_school, "Updated High")
        self.assertEqual(self.folder.student_ps, "REUSABLE")
        self.assertEqual(self.file.student_ps, "REUSABLE")
        self.assertEqual(self.file.r2_key, "qa/owner-original.pdf")

    def test_explicit_unchanged_identifier_accepts_own_files(self):
        response = self._patch_profile({"ps_number": "REUSABLE", "high_school": "Updated High"})
        self.assertEqual(response.status_code, 200)
        self.student.refresh_from_db()
        self.assertEqual(self.student.ps_number, "REUSABLE")
        self.assertEqual(self.student.high_school, "Updated High")

    def test_profile_update_cannot_claim_orphan_file_namespace(self):
        orphan = InventoryFolder.objects.create(
            tenant=self.tenant, scope="student", student_ps="ORPHAN", name="원본 자료",
        )
        response = self._patch_profile({"ps_number": "ORPHAN"})
        self.assertEqual(response.status_code, 400)
        self.student.refresh_from_db()
        orphan.refresh_from_db()
        self.assertEqual(self.student.ps_number, "REUSABLE")
        self.assertEqual(orphan.student_ps, "ORPHAN")

    def test_foreign_student_exclusion_cannot_claim_orphan_namespace(self):
        other_tenant = _create_tenant(code="inventory-exclusion-other")
        foreign_student = _create_student(other_tenant, "ORPHAN", parent_phone="")
        InventoryFolder.objects.create(
            tenant=self.tenant, scope="student", student_ps="ORPHAN", name="원본 자료",
        )
        self.assertTrue(student_login_id_taken(
            tenant=self.tenant, display_username="ORPHAN",
            exclude_student_id=foreign_student.pk, exclude_user_id=foreign_student.user_id,
        ))

    def test_deleted_student_exclusion_does_not_release_preserved_namespace(self):
        soft_delete_student(self.student, tenant=self.tenant)
        self.student.refresh_from_db()
        self.assertTrue(student_login_id_taken(
            tenant=self.tenant, display_username=self.student.ps_number,
            exclude_student_id=self.student.pk, exclude_user_id=self.student.user_id,
        ))

    def test_own_namespace_still_checks_conflicting_login_user(self):
        legacy_user = apps.get_model("core", "User").objects.create_user(
            tenant=self.tenant, username="REUSABLE", password="test1234",
        )
        apps.get_model("core", "TenantMembership").ensure_active(
            tenant=self.tenant, user=legacy_user, role="teacher",
        )
        self.assertTrue(student_login_id_taken(
            tenant=self.tenant, display_username="REUSABLE",
            exclude_student_id=self.student.pk, exclude_user_id=self.student.user_id,
        ))

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
