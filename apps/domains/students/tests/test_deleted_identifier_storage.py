"""Public lifecycle regression for full-length reversible deletion identifiers."""
from importlib import import_module
from types import SimpleNamespace
from unittest import skipUnless
from unittest.mock import patch

from django.apps import apps
from django.contrib.auth import get_user_model
from django.db import connection
from django.db.migrations.exceptions import IrreversibleError
from django.db.migrations.state import ProjectState
from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework.exceptions import ValidationError
from rest_framework_simplejwt.tokens import AccessToken

from apps.core.models import Tenant, TenantMembership
from apps.core.models.user import user_internal_username
from apps.domains.students.models import Student
from apps.domains.students.serializers import StudentCreateSerializer, StudentUpdateSerializer
from apps.domains.students.services.creation import create_student_account
from apps.domains.students.services.identity import StudentIdentityError, resolve_student_login_id
from apps.domains.students.tests.test_identity_lifecycle import _create_student
from apps.support.students.lifecycle_dependencies import ensure_parent_account_for_student


class DeletedIdentifierStorageTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(code="qa-deleted-id", name="QA", account_password_policy={"parent_mode": "phone_last4"})
        self.owner = get_user_model().objects.create_user(username="qa-deleted-id-owner", tenant=self.tenant)
        TenantMembership.ensure_active(tenant=self.tenant, user=self.owner, role="owner")
        token = AccessToken.for_user(self.owner)
        token["tenant_id"] = self.tenant.pk
        token["token_version"] = self.owner.token_version or 0
        self.client = APIClient(HTTP_HOST="api.hakwonplus.com", HTTP_X_TENANT_CODE=self.tenant.code)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        self.parent = ensure_parent_account_for_student(tenant=self.tenant,
            parent_phone="01098765432", student_name="QA", initial_password="qa-parent-pass").parent

    def test_public_delete_restore_preserves_max_identifier_inventory_and_result(self):
        Enrollment = apps.get_model("enrollment", "Enrollment")
        Lecture = apps.get_model("lectures", "Lecture")
        InventoryFile = apps.get_model("inventory", "InventoryFile")
        InventoryFolder = apps.get_model("inventory", "InventoryFolder")
        Result = apps.get_model("results", "Result")

        foreign_tenant = Tenant.objects.create(code="qa-deleted-id-foreign", name="Other")
        foreign = _create_student(foreign_tenant, "foreign-id")
        for index, original in enumerate(("qa-sp-teacher-score-1770000000000-1234-1", "x" * 50, "가" * 50)):
            with self.subTest(length=len(original), unicode=original.startswith("가")):
                user = get_user_model().objects.create_user(username=user_internal_username(self.tenant, original), tenant=self.tenant, password="qa-student-pass")
                student = Student.objects.create(id=2**63 - 1 - index, tenant=self.tenant, user=user,
                    ps_number=original, name="QA", parent=self.parent, parent_phone="01098765432", omr_code="98765432")
                TenantMembership.ensure_active(tenant=self.tenant, user=user, role="student")
                lecture = Lecture.objects.create(tenant=self.tenant, title=f"QA {index}", name="QA")
                enrollment = Enrollment.objects.create(tenant=self.tenant, student=student, lecture=lecture, status="ACTIVE")
                result = Result.objects.create(target_type="exam", target_id=100 + index,
                    enrollment=enrollment, total_score=73, max_score=100)
                folder = InventoryFolder.objects.create(tenant=self.tenant, scope="student", student_ps=original, name="Authored")
                file = InventoryFile.objects.create(tenant=self.tenant, scope="student", student_ps=original,
                    folder=folder, display_name="Authored", original_name="saved.pdf", r2_key=f"qa/unchanged/{index}")
                response = self.client.post("/api/v1/students/bulk_delete/", {"ids": [student.pk, foreign.pk]}, format="json")
                self.assertEqual(response.status_code, 200, getattr(response, "data", None))
                self.assertEqual(response.data["deleted"], 1)
                tombstone = f"_del_{student.pk}_{original}"
                self.assertLessEqual(len(tombstone), 75)
                student.refresh_from_db(); folder.refresh_from_db(); file.refresh_from_db(); result.refresh_from_db()
                self.assertEqual((student.ps_number, folder.student_ps, file.student_ps), (tombstone,) * 3)
                self.assertEqual((result.total_score, result.enrollment_id, file.r2_key), (73, enrollment.pk, f"qa/unchanged/{index}"))
                user.refresh_from_db()
                self.assertFalse(user.is_active)
                self.assertEqual(self.client.post("/api/v1/students/bulk_delete/", {"ids": [student.pk]}, format="json").data["deleted"], 0)
                restored = self.client.post("/api/v1/students/bulk_restore/", {"ids": [student.pk]}, format="json")
                self.assertEqual(restored.status_code, 200, restored.data)
                self.assertEqual(restored.data["restored"], 1, restored.data)
                student.refresh_from_db(); folder.refresh_from_db(); file.refresh_from_db(); user.refresh_from_db(); enrollment.refresh_from_db()
                self.assertEqual((student.ps_number, folder.student_ps, file.student_ps), (original,) * 3)
                self.assertTrue(user.is_active)
                self.assertTrue(user.check_password("qa-student-pass"))
                self.assertEqual(student.parent_id, self.parent.pk)
                self.assertEqual(user.username, user_internal_username(self.tenant, original))
                self.assertEqual(enrollment.status, "ACTIVE")
                self.assertEqual(student.parent.phone, "01098765432")
                self.assertEqual(file.r2_key, f"qa/unchanged/{index}")
        foreign.refresh_from_db()
        self.assertIsNone(foreign.deleted_at)
        self.assertEqual(foreign.ps_number, "foreign-id")

    def test_public_and_service_normal_identifiers_remain_limited_to_50(self):
        oversized = "x" * 51
        for serializer in (StudentCreateSerializer(), StudentUpdateSerializer()):
            with self.assertRaises(ValidationError) as error:
                serializer.fields["ps_number"].run_validation(oversized)
            self.assertEqual(error.exception.get_codes(), ["max_length"])
        with self.assertRaises(StudentIdentityError):
            resolve_student_login_id(tenant=self.tenant, requested_id=oversized)
        with self.assertRaises(StudentIdentityError):
            create_student_account(tenant=self.tenant, student_data={"ps_number": oversized}, password="qa-password")
        student = _create_student(self.tenant, "normal")
        student.ps_number = oversized
        with self.assertRaises(ValueError):
            student.save(update_fields=["ps_number"])

    def test_student_self_username_rejects_invalid_input_before_other_writes(self):
        student = _create_student(self.tenant, "self-login")
        TenantMembership.ensure_active(tenant=self.tenant, user=student.user, role="student")
        token = AccessToken.for_user(student.user)
        token["tenant_id"] = self.tenant.pk
        token["token_version"] = student.user.token_version or 0
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        before = (student.ps_number, student.name, student.user.username, student.user.password,
                  student.user.tenant_id, student.user.token_version)
        for invalid in ("x" * 51, "  " + "가" * 51 + "  ", 123, True, [], {}):
            with self.subTest(kind=type(invalid).__name__):
                response = self.client.patch("/api/v1/students/me/",
                    {"username": invalid, "name": "must not change"}, format="json")
                self.assertEqual(response.status_code, 400)
                student.refresh_from_db()
                student.user.refresh_from_db()
                self.assertEqual((student.ps_number, student.name, student.user.username,
                    student.user.password, student.user.tenant_id, student.user.token_version), before)
        with patch("apps.domains.messaging.policy.send_alimtalk_via_owner", return_value=True):
            response = self.client.patch("/api/v1/students/me/", {"username": "  " + "z" * 50 + "  "}, format="json")
        self.assertEqual(response.status_code, 200)
        student.refresh_from_db()
        student.user.refresh_from_db()
        self.assertEqual(student.ps_number, "z" * 50)
        self.assertEqual(student.user.username, user_internal_username(self.tenant, "z" * 50))
        self.assertEqual(student.user.tenant_id, self.tenant.pk)

    @skipUnless(connection.vendor == "postgresql", "PostgreSQL varchar expansion and shrink behavior")
    def test_real_expand_and_reverse_guard_prevent_truncation(self):
        InventoryFolder = apps.get_model("inventory", "InventoryFolder")
        InventoryFile = apps.get_model("inventory", "InventoryFile")

        student = _create_student(self.tenant, "x" * 50)
        folder = InventoryFolder.objects.create(tenant=self.tenant, scope="student", student_ps=student.ps_number, name="Authored")
        file = InventoryFile.objects.create(tenant=self.tenant, scope="student", student_ps=student.ps_number,
            folder=folder, display_name="Authored", original_name="saved.pdf", r2_key="qa/migration-unchanged")
        migrations = [import_module(f"apps.domains.{app}.migrations.{name}").Migration(name, app)
            for app, name in (("students", "0022_student_deleted_identifier_storage"),
                              ("inventory", "0003_student_deleted_identifier_storage"))]
        state = ProjectState.from_apps(apps)
        for app, model, field_name in (("students", "student", "ps_number"),
                                       ("inventory", "inventoryfolder", "student_ps"),
                                       ("inventory", "inventoryfile", "student_ps")):
            field = state.models[app, model].fields[field_name].clone()
            field.max_length = 50
            state.alter_field(app, model, field_name, field, preserve_default=True)
        with connection.cursor() as cursor:
            # TestCase wraps fixture inserts in one transaction; settle their
            # deferred FK checks before testing DDL as production migrations do.
            cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
            for table, column in (("students_student", "ps_number"),
                                  ("inventory_inventoryfolder", "student_ps"),
                                  ("inventory_inventoryfile", "student_ps")):
                cursor.execute(f'ALTER TABLE "{table}" ALTER COLUMN "{column}" TYPE varchar(50)')
        before_states = {}
        with connection.schema_editor() as editor:
            for migration in migrations:
                before_states[migration.app_label] = state.clone()
                state = migration.apply(state, editor)
        statements = []

        def record(execute, sql, params, many, context):
            upper = sql.upper()
            if upper.startswith("LOCK TABLE") or upper.startswith("ALTER TABLE") or "LENGTH(" in upper:
                statements.append(upper)
            return execute(sql, params, many, context)

        with connection.execute_wrapper(record), connection.schema_editor() as editor:
            for migration in reversed(migrations):
                migration.unapply(before_states[migration.app_label], editor)
        # Both inventory tables lock before either length scan, then narrowing.
        for table in ("students_student", "inventory_inventoryfile", "inventory_inventoryfolder"):
            lock = next(index for index, sql in enumerate(statements) if sql.startswith("LOCK TABLE") and table.upper() in sql)
            scan = next(index for index, sql in enumerate(statements) if "LENGTH(" in sql and table.upper() in sql)
            alter = next(index for index, sql in enumerate(statements) if sql.startswith("ALTER TABLE") and table.upper() in sql)
            self.assertLess(lock, scan)
            self.assertLess(scan, alter)
        inventory_lock = next(sql for sql in statements if sql.startswith("LOCK TABLE") and "INVENTORY_" in sql)
        self.assertLess(inventory_lock.index("INVENTORY_INVENTORYFILE"), inventory_lock.index("INVENTORY_INVENTORYFOLDER"))
        with connection.cursor() as cursor:
            cursor.execute("SELECT character_maximum_length FROM information_schema.columns WHERE "
                "(table_name = 'students_student' AND column_name = 'ps_number') OR "
                "(table_name IN ('inventory_inventoryfile', 'inventory_inventoryfolder') AND column_name = 'student_ps')")
            self.assertEqual(sorted(row[0] for row in cursor.fetchall()), [50, 50, 50])
        state = before_states["students"].clone()
        with connection.schema_editor() as editor:
            for migration in migrations:
                state = migration.apply(state, editor)
        tombstone = "_del_9223372036854775807_" + student.ps_number
        self.assertEqual(len(tombstone), 75)
        Student.objects.filter(pk=student.pk).update(ps_number=tombstone)
        InventoryFolder.objects.filter(pk=folder.pk).update(student_ps=tombstone)
        InventoryFile.objects.filter(pk=file.pk).update(student_ps=tombstone)
        for migration in migrations:
            # A long row refuses reversal while exclusive locks prevent a writer
            # from slipping between the scan and any narrowing SQL.
            with self.assertRaises(IrreversibleError), connection.schema_editor() as editor:
                migration.unapply(before_states[migration.app_label], editor)
        student.refresh_from_db(); folder.refresh_from_db(); file.refresh_from_db()
        self.assertEqual((student.ps_number, folder.student_ps, file.student_ps), (tombstone,) * 3)
        self.assertEqual(file.r2_key, "qa/migration-unchanged")

    def test_reverse_guard_allows_short_values_and_refuses_each_long_namespace(self):
        InventoryFolder = apps.get_model("inventory", "InventoryFolder")
        InventoryFile = apps.get_model("inventory", "InventoryFile")
        student = _create_student(self.tenant, "x" * 50)
        folder = InventoryFolder.objects.create(tenant=self.tenant, scope="student", student_ps=student.ps_number, name="Authored")
        file = InventoryFile.objects.create(tenant=self.tenant, scope="student", student_ps=student.ps_number,
            folder=folder, display_name="Authored", original_name="saved.pdf", r2_key="qa/guard-unchanged")
        student_guard = import_module("apps.domains.students.migrations.0022_student_deleted_identifier_storage").guard_identifier_shrink
        inventory_guard = import_module("apps.domains.inventory.migrations.0003_student_deleted_identifier_storage").guard_identifier_shrink
        editor = connection.schema_editor()
        for model, row, field, guard in ((Student, student, "ps_number", student_guard),
                                       (InventoryFolder, folder, "student_ps", inventory_guard),
                                       (InventoryFile, file, "student_ps", inventory_guard)):
            with self.subTest(model=model.__name__):
                guard(apps, editor)
                with self.assertRaises(IrreversibleError):
                    guard(apps, SimpleNamespace(connection=connection, atomic_migration=False))
                tombstone = "_del_9223372036854775807_" + "x" * 50
                model.objects.filter(pk=row.pk).update(**{field: tombstone})
                with self.assertRaises(IrreversibleError):
                    guard(apps, editor)
                self.assertEqual(model.objects.values_list(field, flat=True).get(pk=row.pk), tombstone)
                model.objects.filter(pk=row.pk).update(**{field: "x" * 50})
                guard(apps, editor)
