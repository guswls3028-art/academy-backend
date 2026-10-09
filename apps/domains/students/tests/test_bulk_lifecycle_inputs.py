import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from apps.core.models import Tenant, TenantMembership
from apps.core.models.user import user_internal_username
from apps.domains.parents.test_support import create_parent_account_fixture
from apps.domains.students.models import Student
from apps.domains.students.services import (
    StudentLifecycleError,
    permanently_delete_students,
    soft_delete_student,
)


class BulkStudentLifecycleInputTests(TestCase):
    actions = ("bulk_delete", "bulk_restore", "bulk_permanent_delete")

    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(code="qa-bulk-lifecycle", name="QA lifecycle")
        cls.foreign = Tenant.objects.create(code="qa-bulk-lifecycle-other", name="QA other")
        cls.owner = get_user_model().objects.create_user(username="qa-bulk-owner", tenant=cls.tenant)
        TenantMembership.ensure_active(tenant=cls.tenant, user=cls.owner, role="owner")
        cls.students = []
        for index, tenant in enumerate((cls.tenant, cls.tenant, cls.foreign), 1):
            parent = create_parent_account_fixture(
                tenant=tenant, parent_phone=f"0109876543{index}",
                student_name=f"QA {index}", initial_password="qa-parent-pass",
            ).parent
            user = get_user_model().objects.create_user(
                username=user_internal_username(tenant, f"QA{index}"),
                tenant=tenant, password="qa-student-pass",
            )
            student = Student.objects.create(
                tenant=tenant, user=user, parent=parent, ps_number=f"QA{index}",
                name=f"QA {index}", phone=f"0101234567{index}",
                parent_phone=parent.phone, omr_code=f"1234567{index}", school_type="HIGH", grade=1,
            )
            TenantMembership.ensure_active(tenant=tenant, user=user, role="student")
            cls.students.append(student)
        cls.active, cls.deleted, cls.other = cls.students
        soft_delete_student(cls.deleted, tenant=cls.tenant)
        soft_delete_student(cls.other, tenant=cls.foreign)

    def setUp(self):
        token = AccessToken.for_user(self.owner)
        token["tenant_id"] = self.tenant.pk
        token["token_version"] = self.owner.token_version or 0
        self.client = APIClient(HTTP_HOST="api.hakwonplus.com", HTTP_X_TENANT_CODE=self.tenant.code)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        self.client.raise_request_exception = False

    def post(self, action, body):
        return self.client.generic(
            "POST", f"/api/v1/students/{action}/", json.dumps(body), content_type="application/json",
        )

    def snapshot(self):
        return (
            list(Student.objects.order_by("id").values_list("id", "ps_number", "deleted_at", "user_id")),
            list(get_user_model().objects.order_by("id").values_list("id", "is_active", "password", "token_version")),
            list(TenantMembership.objects.order_by("id").values_list("id", "is_active", "role")),
        )

    def test_non_object_bodies_are_rejected_without_changes(self):
        before = self.snapshot()
        for action in self.actions:
            for body in (None, [], [self.active.pk], "ids", 1, True):
                with self.subTest(action=action, body=body):
                    self.assertEqual(self.post(action, body).status_code, 400)
                    self.assertEqual(self.snapshot(), before)

    def test_any_invalid_id_rejects_the_whole_selection_before_mutation(self):
        for action in self.actions:
            selected = self.active if action == "bulk_delete" else self.deleted
            for invalid in (True, False, None, {}, [], 0, -1, 1.5, float(selected.pk),
                            "bad", "1.5", "²", 2**63, str(2**63), "9" * 5000):
                with self.subTest(action=action, invalid_type=type(invalid).__name__, value=str(invalid)[:22]):
                    with transaction.atomic():
                        before = self.snapshot()
                        try:
                            response = self.post(action, {"ids": [selected.pk, invalid]})
                            self.assertEqual(response.status_code, 400)
                            self.assertEqual(self.snapshot(), before)
                        finally:
                            transaction.set_rollback(True)

    def test_ids_container_and_empty_selection_are_rejected(self):
        before = self.snapshot()
        for action in self.actions:
            for body in ({}, {"ids": None}, {"ids": []}, {"ids": "1"}, {"ids": {}}, {"ids": True}):
                with self.subTest(action=action, body=body):
                    self.assertEqual(self.post(action, body).status_code, 400)
                    self.assertEqual(self.snapshot(), before)

    def test_valid_numeric_strings_duplicates_and_retry_preserve_normal_lifecycle(self):
        original_password = self.active.user.password
        selection = [str(self.active.pk), self.active.pk, self.other.pk]
        deleted = self.post("bulk_delete", {"ids": selection})
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(deleted.data["deleted"], 1)
        self.active.refresh_from_db()
        self.assertIsNotNone(self.active.deleted_at)
        self.assertEqual(self.post("bulk_delete", {"ids": selection}).data["deleted"], 0)
        detail = self.client.get(f"/api/v1/students/{self.active.pk}/")
        self.assertEqual(detail.status_code, 200)
        self.assertIsNotNone(detail.data["deleted_at"])
        active_list = self.client.get("/api/v1/students/")
        self.assertEqual(active_list.status_code, 200)
        self.assertNotIn(self.active.pk, [row["id"] for row in active_list.data["results"]])
        restored = self.post("bulk_restore", {"ids": selection})
        self.assertEqual(restored.status_code, 200)
        self.assertEqual(restored.data["restored"], 1)
        self.active.refresh_from_db()
        self.active.user.refresh_from_db()
        self.assertIsNone(self.active.deleted_at)
        self.assertEqual(self.active.user.password, original_password)
        self.assertTrue(self.active.user.is_active)
        self.assertEqual(self.client.get(f"/api/v1/students/{self.active.pk}/").status_code, 200)
        self.assertEqual(self.post("bulk_restore", {"ids": selection}).data["restored"], 0)
        self.assertEqual(self.post("bulk_delete", {"ids": selection}).data["deleted"], 1)
        permanently = self.post("bulk_permanent_delete", {"ids": selection})
        self.assertEqual(permanently.status_code, 200)
        self.assertEqual(permanently.data["deleted"], 1)
        self.assertFalse(Student.objects.filter(pk=self.active.pk).exists())
        self.assertTrue(Student.objects.filter(pk=self.other.pk, tenant=self.foreign).exists())
        self.assertEqual(self.post("bulk_permanent_delete", {"ids": selection}).data["deleted"], 0)

    def test_soft_delete_existing_limit_and_current_membership_are_preserved(self):
        before = self.snapshot()
        self.assertEqual(self.post("bulk_delete", {"ids": [self.active.pk] * 201}).status_code, 400)
        TenantMembership.objects.filter(tenant=self.tenant, user=self.owner).update(is_active=False)
        for action in self.actions:
            with self.subTest(action=action):
                self.assertEqual(self.post(action, {"ids": [self.active.pk]}).status_code, 401)
        self.assertEqual(self.snapshot()[:2], before[:2])

    def test_permanent_delete_service_does_not_coerce_or_drop_invalid_ids(self):
        for invalid in (True, False, self.deleted.pk + 0.5, float("inf"), {}, None, -1, 2**63, "²"):
            with self.subTest(invalid=invalid):
                with transaction.atomic():
                    before = self.snapshot()
                    try:
                        with self.assertRaises(StudentLifecycleError) as caught:
                            permanently_delete_students(tenant=self.tenant, student_ids=[self.deleted.pk, invalid])
                        self.assertEqual(caught.exception.code, "invalid_student_ids")
                        self.assertEqual(self.snapshot(), before)
                    finally:
                        transaction.set_rollback(True)

    def test_permanent_delete_unexpected_error_returns_safe_retry_message(self):
        with patch("apps.domains.students.views.student_views.permanently_delete_students",
                   side_effect=RuntimeError("private-storage-key-qa")):
            response = self.post("bulk_permanent_delete", {"ids": [self.deleted.pk]})
        self.assertEqual(response.status_code, 500)
        self.assertNotIn("private-storage-key-qa", response.content.decode())
        self.assertTrue(Student.objects.filter(pk=self.deleted.pk).exists())

    def test_import_conflict_rejects_fractional_ids_before_selecting_a_student(self):
        for invalid in (self.deleted.pk + 0.5, True, 2**63, "²"):
            with self.subTest(invalid=invalid):
                with transaction.atomic():
                    before = self.snapshot()
                    try:
                        response = self.post("bulk_resolve_conflicts", {
                            "resolutions": [{"row": 2, "student_id": invalid, "action": "restore"}],
                        })
                        self.assertEqual(response.status_code, 200)
                        self.assertEqual(response.data["restored"], 0)
                        self.assertEqual(len(response.data["failed"]), 1)
                        self.assertEqual(self.snapshot(), before)
                    finally:
                        transaction.set_rollback(True)

    def test_import_conflict_bad_row_preserves_valid_following_restore(self):
        response = self.post("bulk_resolve_conflicts", {"resolutions": [
            {"row": 2, "student_id": self.deleted.pk + 0.5, "action": "restore"},
            {"row": 3, "student_id": str(self.deleted.pk), "action": "restore"},
        ]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["restored"], 1)
        self.assertEqual([row["row"] for row in response.data["failed"]], [2])
        self.assertEqual([row["row"] for row in response.data["resolved"]], [3])
        self.deleted.refresh_from_db()
        self.assertIsNone(self.deleted.deleted_at)

    def test_import_conflict_unexpected_error_does_not_disclose_storage_details(self):
        with patch("apps.domains.students.services.import_students.student_repo.student_filter_tenant_id_deleted_first",
                   side_effect=RuntimeError("private-storage-key-qa")):
            response = self.post("bulk_resolve_conflicts", {"resolutions": [
                {"row": 2, "student_id": self.deleted.pk, "action": "restore"},
            ]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["failed"]), 1)
        self.assertNotIn("private-storage-key-qa", response.content.decode())
        self.assertTrue(Student.objects.filter(pk=self.deleted.pk).exists())
