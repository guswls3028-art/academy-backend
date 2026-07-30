from __future__ import annotations

from io import StringIO
import re
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command, CommandError
from django.test import TestCase
from django.utils import timezone

from apps.core.models import Tenant
from apps.domains.exams.models import Exam
from apps.domains.fees.models import FeeTemplate, StudentFee
from apps.domains.inventory.models import InventoryFile
from apps.domains.matchup.models import MatchupDocument
from apps.domains.messaging.models import MessageTemplate
from apps.domains.results.models import Result
from apps.domains.students.models import Student
from apps.domains.submissions.models import Submission


User = get_user_model()


class CleanupE2EResidueTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            code="cleanup-e2e",
            name="Cleanup E2E",
            is_active=True,
        )

    def execute_cleanup(self):
        dry_run = StringIO()
        call_command(
            "cleanup_e2e_residue",
            "--tenant-id",
            str(self.tenant.id),
            "--dry-run",
            stdout=dry_run,
        )
        token_match = re.search(
            r"확인 토큰 \(exact targets\): ([0-9a-f]{64})",
            dry_run.getvalue(),
        )
        self.assertIsNotNone(token_match)
        call_command(
            "cleanup_e2e_residue",
            "--tenant-id",
            str(self.tenant.id),
            "--execute",
            "--confirm-token",
            token_match.group(1),
            stdout=StringIO(),
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

        self.execute_cleanup()

        self.assertFalse(FeeTemplate.objects.filter(id=unreferenced.id).exists())
        referenced.refresh_from_db()
        self.assertFalse(referenced.is_active)
        self.assertFalse(referenced.auto_assign)
        self.assertTrue(StudentFee.objects.filter(fee_template=referenced).exists())

    def test_execute_requires_exact_dry_run_token(self):
        MessageTemplate.objects.create(
            tenant=self.tenant,
            category="default",
            name="[E2E-123456] Token guard",
            body="guard",
            is_system=False,
        )

        with self.assertRaisesMessage(CommandError, "확인 토큰"):
            call_command(
                "cleanup_e2e_residue",
                "--tenant-id",
                str(self.tenant.id),
                "--execute",
                stdout=StringIO(),
            )

    def test_soft_deleted_student_uses_permanent_lifecycle_cleanup(self):
        user = User.objects.create_user(
            tenant=self.tenant,
            username="e2e-residue-user",
            password="test1234",
            is_active=False,
        )
        student = Student.objects.create(
            tenant=self.tenant,
            user=user,
            ps_number="_del_999_e2e123456",
            omr_code="12345678",
            name="[E2E-123456] Student",
            parent_phone="01000000000",
            deleted_at=timezone.now(),
        )

        self.execute_cleanup()

        self.assertFalse(Student.objects.filter(id=student.id).exists())
        self.assertFalse(User.objects.filter(id=user.id).exists())

    @patch(
        "apps.infrastructure.storage.r2.head_object_r2_storage",
        return_value=(False, 0),
    )
    @patch("apps.infrastructure.storage.r2.delete_object_r2_storage")
    def test_matchup_cleanup_removes_inventory_and_r2_before_db(
        self,
        delete_object,
        _head_object,
    ):
        key = f"tenants/{self.tenant.id}/matchup/e2e-source.pdf"
        inventory = InventoryFile.objects.create(
            tenant=self.tenant,
            scope="admin",
            display_name="[E2E-123456] Matchup source",
            r2_key=key,
            original_name="source.pdf",
            size_bytes=123,
            content_type="application/pdf",
        )
        document = MatchupDocument.objects.create(
            tenant=self.tenant,
            inventory_file=inventory,
            title="[E2E-123456] Matchup source",
            r2_key=key,
            original_name="source.pdf",
            size_bytes=123,
            content_type="application/pdf",
            status="done",
        )

        self.execute_cleanup()

        self.assertFalse(MatchupDocument.objects.filter(id=document.id).exists())
        self.assertFalse(InventoryFile.objects.filter(id=inventory.id).exists())
        delete_object.assert_called_once_with(key=key)

    def test_exam_cleanup_removes_generic_result_and_submission_rows(self):
        user = User.objects.create_user(
            tenant=self.tenant,
            username="cleanup-e2e-exam-user",
            password="test1234",
        )
        exam = Exam.objects.create(
            tenant=self.tenant,
            title="[E2E-123456] Clinic remediation exam",
            exam_type=Exam.ExamType.REGULAR,
            max_score=100,
            pass_score=80,
        )
        result = Result.objects.create(
            target_type="exam",
            target_id=exam.id,
            total_score=20,
            max_score=100,
        )
        submission = Submission.objects.create(
            tenant=self.tenant,
            user=user,
            target_type=Submission.TargetType.EXAM,
            target_id=exam.id,
            source=Submission.Source.ONLINE,
            status=Submission.Status.DONE,
        )

        self.execute_cleanup()

        self.assertFalse(Exam.objects.filter(id=exam.id).exists())
        self.assertFalse(Result.objects.filter(id=result.id).exists())
        self.assertFalse(Submission.objects.filter(id=submission.id).exists())

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

        self.execute_cleanup()

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

        self.execute_cleanup()

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

        self.execute_cleanup()

        self.assertTrue(Student.objects.filter(id=student.id).exists())
