from __future__ import annotations

from django.contrib.auth import get_user_model
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

from apps.core.models import Tenant


class RegistrationRequestHistoryMigrationTests(TransactionTestCase):
    migrate_from = ("students", "0018_student_support_session")
    migrate_to = ("students", "0019_registration_request_student_history")

    def setUp(self):
        super().setUp()
        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_from])
        old_apps = executor.loader.project_state([self.migrate_from]).apps
        Student = old_apps.get_model("students", "Student")
        Registration = old_apps.get_model("students", "StudentRegistrationRequest")

        tenant = Tenant.objects.create(
            name="가입 이력 마이그레이션 학원",
            code="registration-history-migration",
            is_active=True,
        )
        user = get_user_model().objects.create(
            username="registration-history-student",
            password="!",
            tenant_id=tenant.id,
            is_active=True,
        )
        student = Student.objects.create(
            tenant_id=tenant.id,
            user_id=user.id,
            ps_number="REG-HISTORY-001",
            omr_code="12345678",
            name="가입이력학생",
            parent_phone="01099998888",
        )
        registration = Registration.objects.create(
            tenant_id=tenant.id,
            status="approved",
            initial_password="!",
            initial_password_plain="",
            name=student.name,
            username=student.ps_number,
            parent_phone=student.parent_phone,
            school_type="HIGH",
            student_id=student.id,
        )
        self.old_registration_model = Registration
        self.tenant_id = tenant.id
        self.student_id = student.id
        self.registration_id = registration.id

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def test_forward_preserves_link_and_old_runtime_insert_remains_compatible(self):
        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_to])
        apps = executor.loader.project_state([self.migrate_to]).apps
        Registration = apps.get_model("students", "StudentRegistrationRequest")

        preserved = Registration.objects.get(pk=self.registration_id)
        self.assertEqual(preserved.student_id, self.student_id)

        second = self.old_registration_model.objects.create(
            tenant_id=self.tenant_id,
            status="approved",
            initial_password="!",
            initial_password_plain="",
            name="가입이력학생",
            username="REG-HISTORY-RETRY",
            parent_phone="01099998888",
            school_type="HIGH",
            student_id=self.student_id,
        )
        try:
            self.assertEqual(
                set(
                    Registration.objects.filter(student_id=self.student_id).values_list(
                        "id", flat=True
                    )
                ),
                {self.registration_id, second.id},
            )
        finally:
            Registration.objects.filter(pk=second.id).delete()
