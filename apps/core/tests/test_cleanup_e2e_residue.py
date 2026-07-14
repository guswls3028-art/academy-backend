from __future__ import annotations

from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase

from apps.core.models import Tenant
from apps.domains.fees.models import FeeTemplate, StudentFee
from apps.domains.messaging.models import MessageTemplate
from apps.domains.students.models import Student


User = get_user_model()


class CleanupE2EResidueTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            code="cleanup-e2e",
            name="Cleanup E2E",
            is_active=True,
        )

    def test_fee_templates_delete_only_when_unreferenced(self):
        unreferenced = FeeTemplate.objects.create(
            tenant=self.tenant,
            name="[E2E-123456] Smoke Fee",
            fee_type=FeeTemplate.FeeType.TUITION,
            amount=1000,
        )
        referenced = FeeTemplate.objects.create(
            tenant=self.tenant,
            name="[E2E-234567] Linked Fee",
            fee_type=FeeTemplate.FeeType.TUITION,
            amount=2000,
            is_active=True,
            auto_assign=True,
        )
        user = User.objects.create_user(
            tenant=self.tenant,
            username="cleanup-e2e-student",
            password="test1234",
        )
        student = Student.objects.create(
            tenant=self.tenant,
            user=user,
            ps_number="CLEAN001",
            omr_code="CLN001",
            name="Cleanup Student",
            parent_phone="010-0000-0001",
        )
        StudentFee.objects.create(
            tenant=self.tenant,
            student=student,
            fee_template=referenced,
        )

        call_command(
            "cleanup_e2e_residue",
            "--tenant-id",
            str(self.tenant.id),
            "--execute",
            stdout=StringIO(),
        )

        self.assertFalse(FeeTemplate.objects.filter(id=unreferenced.id).exists())
        referenced.refresh_from_db()
        self.assertFalse(referenced.is_active)
        self.assertFalse(referenced.auto_assign)
        self.assertTrue(StudentFee.objects.filter(fee_template=referenced).exists())

    def test_only_explicit_e2e_template_residue_is_removed(self):
        residue = MessageTemplate.objects.create(
            tenant=self.tenant,
            category="default",
            name="복사 - 복사 - 복사 - [E2E-123456] 출석 안내",
            body="E2E residue",
            is_system=False,
        )
        normal_copy = MessageTemplate.objects.create(
            tenant=self.tenant,
            category="default",
            name="복사 - 학부모 안내",
            body="사용자 문구",
            is_system=False,
        )

        call_command(
            "cleanup_e2e_residue",
            "--tenant-id",
            str(self.tenant.id),
            "--execute",
            stdout=StringIO(),
        )

        self.assertFalse(MessageTemplate.objects.filter(id=residue.id).exists())
        self.assertTrue(MessageTemplate.objects.filter(id=normal_copy.id).exists())

    def test_recursive_copy_prefix_without_e2e_marker_is_preserved(self):
        legitimate = MessageTemplate.objects.create(
            tenant=self.tenant,
            category="default",
            name="복사 - 복사 - 복사 - 정식 학부모 안내",
            body="사용자가 반복 복제한 정식 문구",
            is_system=False,
        )

        call_command(
            "cleanup_e2e_residue",
            "--tenant-id",
            str(self.tenant.id),
            "--execute",
            stdout=StringIO(),
        )

        self.assertTrue(MessageTemplate.objects.filter(id=legitimate.id).exists())

    def test_recursive_copy_prefix_never_deletes_non_template_business_data(self):
        user = User.objects.create_user(
            tenant=self.tenant,
            username="legitimate-copy-name",
            password="test1234",
        )
        student = Student.objects.create(
            tenant=self.tenant,
            user=user,
            ps_number="COPY001",
            omr_code="CPY001",
            name="복사 - 복사 - 복사 - 정식 등록 학생",
            parent_phone="010-0000-0002",
        )

        call_command(
            "cleanup_e2e_residue",
            "--tenant-id",
            str(self.tenant.id),
            "--execute",
            stdout=StringIO(),
        )

        self.assertTrue(Student.objects.filter(id=student.id).exists())
