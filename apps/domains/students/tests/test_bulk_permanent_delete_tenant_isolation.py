from datetime import timedelta
from io import StringIO

# PATH: apps/domains/students/tests/test_bulk_permanent_delete_tenant_isolation.py
"""
크로스테넌트 보호 증명 테스트 — bulk_permanent_delete

시나리오: User X가 Tenant A(학생), Tenant B(teacher Membership + Submission).
  Tenant A에서 영구삭제 시 Tenant B 데이터 보존 증명.
"""
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken
from rest_framework_simplejwt.tokens import RefreshToken

from apps.core.models import PendingPasswordReset
from apps.core.models.tenant import Tenant
from apps.core.models.tenant_membership import TenantMembership
from apps.core.services.password import create_pending_password_reset
from apps.domains.enrollment.models import Enrollment
from apps.domains.fees.models import FeePayment, FeeTemplate, InvoiceItem, StudentFee, StudentInvoice
from apps.domains.lectures.models import Lecture
from apps.domains.lectures.models import Section, SectionAssignment
from apps.domains.parents.models import Parent
from apps.domains.clinic.models import SessionParticipant
from apps.domains.submissions.models import Submission
from apps.domains.students.models import Student
from apps.domains.students.services import (
    StudentLifecycleError,
    permanently_delete_students,
    soft_delete_student,
)
from apps.domains.students.views import StudentViewSet
from apps.domains.video.models import Video, VideoComment

User = get_user_model()


class TestBulkPermanentDeleteTenantIsolation(TestCase):

    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant_a = Tenant.objects.create(name="Academy A", code="testa", is_active=True)
        self.tenant_b = Tenant.objects.create(name="Academy B", code="testb", is_active=True)

        # User X: Tenant A의 학생
        self.user_x = User.objects.create_user(
            username="t_a_student001", password="test1234",
            tenant=self.tenant_a, phone="01012340000", name="학생X",
        )
        self.student_a = Student.objects.create(
            tenant=self.tenant_a, user=self.user_x,
            ps_number="A001", name="학생X",
            phone="01012340000", parent_phone="01099990000",
            omr_code="99990000",
        )
        TenantMembership.ensure_active(tenant=self.tenant_a, user=self.user_x, role="student")
        # User X: Tenant B teacher 멤버십
        TenantMembership.ensure_active(tenant=self.tenant_b, user=self.user_x, role="teacher")

        # Tenant A 데이터
        self.lecture_a = Lecture.objects.create(tenant=self.tenant_a, name="강의A")
        self.enrollment_a = Enrollment.objects.create(
            tenant=self.tenant_a, student=self.student_a,
            lecture=self.lecture_a, status="ACTIVE",
        )
        self.sub_a = Submission.objects.create(
            tenant=self.tenant_a, user=self.user_x,
            enrollment_id=self.enrollment_a.id,
            target_type="exam", target_id=1,
            source="omr_manual", status="done",
        )

        # Tenant B 데이터 (같은 user의 submission)
        self.sub_b = Submission.objects.create(
            tenant=self.tenant_b, user=self.user_x,
            enrollment_id=None,
            target_type="exam", target_id=2,
            source="omr_manual", status="done",
        )

        # 소프트삭제 (영구삭제 전제조건)
        self.student_a.deleted_at = timezone.now()
        self.student_a.save(update_fields=["deleted_at"])

        # Tenant A admin (Staff 권한)
        self.admin_a = User.objects.create_user(
            username="t_a_admin", password="test1234",
            tenant=self.tenant_a, is_staff=True, name="AdminA",
        )
        TenantMembership.ensure_active(tenant=self.tenant_a, user=self.admin_a, role="owner")

    def _call(self, tenant, admin_user, student_ids):
        request = self.factory.post(
            "/api/v1/students/bulk_permanent_delete/",
            data={"ids": student_ids}, format="json",
        )
        force_authenticate(request, user=admin_user)
        request.tenant = tenant
        view = StudentViewSet.as_view({"post": "bulk_permanent_delete"})
        return view(request)

    def test_delete_preserves_other_tenant_data(self):
        """핵심 증명: Tenant A 영구삭제 → Tenant B 데이터 100% 보존."""
        PendingPasswordReset.objects.create(
            tenant=self.tenant_b,
            user=self.user_x,
            password_hash=make_password("87654321"),
            expires_at=timezone.now() + timedelta(minutes=30),
        )

        # PRE
        self.assertEqual(Submission.objects.filter(tenant=self.tenant_a, user=self.user_x).count(), 1)
        self.assertEqual(Submission.objects.filter(tenant=self.tenant_b, user=self.user_x).count(), 1)
        self.assertTrue(TenantMembership.objects.filter(tenant=self.tenant_b, user=self.user_x).exists())

        # ACT
        resp = self._call(self.tenant_a, self.admin_a, [self.student_a.id])
        self.assertEqual(resp.status_code, 200, f"응답: {resp.data}")
        self.assertEqual(resp.data.get("deleted"), 1)

        # Tenant A 삭제 확인
        self.assertFalse(Student.objects.filter(id=self.student_a.id).exists())
        self.assertFalse(Enrollment.objects.filter(id=self.enrollment_a.id).exists())
        self.assertEqual(Submission.objects.filter(tenant=self.tenant_a, user=self.user_x).count(), 0)
        self.assertFalse(TenantMembership.objects.filter(tenant=self.tenant_a, user=self.user_x).exists())

        # ★ Tenant B 보존 증명
        self.assertEqual(
            Submission.objects.filter(tenant=self.tenant_b, user=self.user_x).count(), 1,
            "❌ CRITICAL: Tenant B submission 삭제됨 = 크로스테넌트 데이터 유실!"
        )
        self.assertTrue(
            TenantMembership.objects.filter(tenant=self.tenant_b, user=self.user_x).exists(),
            "❌ CRITICAL: Tenant B membership 삭제됨 = 다른 학원 접속 불가!"
        )
        self.assertTrue(
            User.objects.filter(id=self.user_x.id).exists(),
            "❌ CRITICAL: 다른 테넌트 멤버십이 있는 User가 삭제됨!"
        )
        self.assertTrue(
            PendingPasswordReset.objects.filter(tenant=self.tenant_b, user=self.user_x).exists(),
            "❌ CRITICAL: 다른 테넌트 pending password reset이 삭제됨!"
        )

    def test_deleted_tenant_pending_reset_removed_even_when_user_retained(self):
        """삭제되는 테넌트의 pending reset은 User가 다른 테넌트에 남아도 정리."""
        create_pending_password_reset(self.user_x, "12345678")

        resp = self._call(self.tenant_a, self.admin_a, [self.student_a.id])

        self.assertEqual(resp.status_code, 200, f"응답: {resp.data}")
        self.assertTrue(User.objects.filter(id=self.user_x.id).exists())
        self.assertFalse(
            PendingPasswordReset.objects.filter(tenant=self.tenant_a, user=self.user_x).exists()
        )

    def test_soft_then_permanent_delete_keeps_cross_tenant_account_active(self):
        """실제 soft delete 경유 후에도 다른 테넌트 계정이 잠기지 않는다."""
        user = User.objects.create_user(
            username="shared_soft_delete",
            password="test1234",
            tenant=self.tenant_a,
            phone="01012121212",
            name="공유계정",
        )
        TenantMembership.ensure_active(tenant=self.tenant_a, user=user, role="student")
        TenantMembership.ensure_active(tenant=self.tenant_b, user=user, role="teacher")
        student = Student.objects.create(
            tenant=self.tenant_a,
            user=user,
            ps_number="A-SHARED",
            name="공유계정학생",
            phone="01012121212",
            parent_phone="01034343434",
            omr_code="12121212",
        )

        soft_delete_student(student, tenant=self.tenant_a)
        user.refresh_from_db()

        self.assertTrue(user.is_active)
        self.assertFalse(TenantMembership.objects.get(tenant=self.tenant_a, user=user).is_active)
        self.assertTrue(TenantMembership.objects.get(tenant=self.tenant_b, user=user).is_active)

        permanently_delete_students(tenant=self.tenant_a, student_ids=[student.id])
        user.refresh_from_db()

        self.assertTrue(user.is_active)
        self.assertFalse(Student.objects.filter(id=student.id).exists())
        self.assertFalse(TenantMembership.objects.filter(tenant=self.tenant_a, user=user).exists())
        self.assertTrue(TenantMembership.objects.filter(tenant=self.tenant_b, user=user).exists())

    def test_user_deleted_when_orphaned(self):
        """멤버십이 전부 삭제되면 User도 삭제."""
        create_pending_password_reset(self.user_x, "12345678")
        refresh = RefreshToken.for_user(self.user_x)
        refresh.blacklist()
        self.assertTrue(OutstandingToken.objects.filter(user=self.user_x).exists())
        self.assertTrue(BlacklistedToken.objects.filter(token__user=self.user_x).exists())
        TenantMembership.objects.filter(tenant=self.tenant_b, user=self.user_x).delete()
        Submission.objects.filter(tenant=self.tenant_b, user=self.user_x).delete()

        resp = self._call(self.tenant_a, self.admin_a, [self.student_a.id])
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(User.objects.filter(id=self.user_x.id).exists(),
                         "orphan User는 삭제되어야 함")
        self.assertFalse(PendingPasswordReset.objects.filter(user=self.user_x).exists())
        self.assertFalse(OutstandingToken.objects.filter(user_id=self.user_x.id).exists())
        self.assertFalse(BlacklistedToken.objects.filter(token__user_id=self.user_x.id).exists())

    def test_same_tenant_parent_profile_keeps_membership_and_user(self):
        """같은 테넌트에 남은 계정 프로필이 있으면 멤버십과 User를 보존."""
        TenantMembership.objects.filter(tenant=self.tenant_b, user=self.user_x).delete()
        Submission.objects.filter(tenant=self.tenant_b, user=self.user_x).delete()
        Parent.objects.create(
            tenant=self.tenant_a,
            user=self.user_x,
            name="학생X 보호자",
            phone="01088889999",
        )
        shared_submission = Submission.objects.create(
            tenant=self.tenant_a,
            user=self.user_x,
            enrollment_id=None,
            target_type="exam",
            target_id=88,
            source="omr_manual",
            status="done",
        )

        resp = self._call(self.tenant_a, self.admin_a, [self.student_a.id])

        self.assertEqual(resp.status_code, 200)
        self.assertFalse(Student.objects.filter(id=self.student_a.id).exists())
        self.assertTrue(User.objects.filter(id=self.user_x.id).exists())
        self.assertTrue(Parent.objects.filter(tenant=self.tenant_a, user=self.user_x).exists())
        self.assertTrue(TenantMembership.objects.filter(tenant=self.tenant_a, user=self.user_x).exists())
        self.assertTrue(Submission.objects.filter(id=shared_submission.id).exists())

    def test_same_tenant_teacher_membership_without_staff_profile_is_preserved(self):
        """학생 프로필 삭제가 같은 테넌트의 staff/teacher 권한을 제거하지 않는다."""
        teacher_user = User.objects.create_user(
            username="student_teacher_shared",
            password="test1234",
            tenant=self.tenant_a,
            is_staff=True,
            name="선생공유",
        )
        TenantMembership.ensure_active(tenant=self.tenant_a, user=teacher_user, role="teacher")
        student = Student.objects.create(
            tenant=self.tenant_a,
            user=teacher_user,
            ps_number="_del_903_TEACHER_SHARED",
            name="선생공유학생",
            phone="",
            parent_phone="01055556666",
            omr_code="55556666",
            deleted_at=timezone.now(),
        )

        permanently_delete_students(tenant=self.tenant_a, student_ids=[student.id])

        self.assertFalse(Student.objects.filter(id=student.id).exists())
        self.assertTrue(User.objects.filter(id=teacher_user.id).exists())
        self.assertTrue(
            TenantMembership.objects.filter(
                tenant=self.tenant_a,
                user=teacher_user,
                role="teacher",
                is_active=True,
            ).exists()
        )

    def test_video_comment_replies_deleted_with_student_comment(self):
        """학생 댓글에 달린 답글이 있어도 영구삭제 그래프가 댓글 트리를 정리."""
        other_user = User.objects.create_user(
            username="video_reply_student",
            password="test1234",
            tenant=self.tenant_a,
            name="댓글학생",
        )
        other_student = Student.objects.create(
            tenant=self.tenant_a,
            user=other_user,
            ps_number="A-VIDEO-REPLY",
            name="댓글학생",
            phone="01023232323",
            parent_phone="01045454545",
            omr_code="23232323",
        )
        video = Video.objects.create(
            tenant=self.tenant_a,
            session=None,
            title="댓글 테스트",
            status=Video.Status.READY,
        )
        parent_comment = VideoComment.objects.create(
            tenant=self.tenant_a,
            video=video,
            author_student=self.student_a,
            content="삭제될 댓글",
        )
        VideoComment.objects.create(
            tenant=self.tenant_a,
            video=video,
            author_student=other_student,
            parent=parent_comment,
            content="답글",
        )

        permanently_delete_students(tenant=self.tenant_a, student_ids=[self.student_a.id])

        self.assertFalse(VideoComment.objects.filter(id=parent_comment.id).exists())
        self.assertFalse(VideoComment.objects.filter(parent_id=parent_comment.id).exists())

    def test_cross_tenant_student_reference_blocks_permanent_delete(self):
        """테넌트가 틀어진 child row는 조용히 삭제하지 않고 중단."""
        SessionParticipant.objects.create(
            tenant=self.tenant_b,
            student=self.student_a,
            status=SessionParticipant.Status.BOOKED,
        )

        with self.assertRaises(StudentLifecycleError) as ctx:
            permanently_delete_students(tenant=self.tenant_a, student_ids=[self.student_a.id])

        self.assertEqual(ctx.exception.code, "cross_tenant_reference")
        self.assertTrue(Student.objects.filter(id=self.student_a.id).exists())

    def test_cross_tenant_student_id_rejected(self):
        """다른 테넌트 학생 ID → deleted=0."""
        other_user = User.objects.create_user(
            username="t_b_stu", password="test1234", tenant=self.tenant_b, name="학생B",
        )
        other_stu = Student.objects.create(
            tenant=self.tenant_b, user=other_user,
            ps_number="B001", name="학생B",
            phone="01055550000", parent_phone="01066660000", omr_code="55550000",
        )
        other_stu.deleted_at = timezone.now()
        other_stu.save(update_fields=["deleted_at"])

        resp = self._call(self.tenant_a, self.admin_a, [other_stu.id])
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data.get("deleted"), 0)
        self.assertTrue(Student.objects.filter(id=other_stu.id).exists())

    def test_service_deletes_fee_and_section_assignment_dependencies(self):
        """영구삭제 SSOT가 현재 Student/Enrollment FK 의존 row를 함께 정리."""
        section = Section.objects.create(
            tenant=self.tenant_a,
            lecture=self.lecture_a,
            label="A",
            section_type="CLASS",
            day_of_week=0,
            start_time="10:00",
        )
        SectionAssignment.objects.create(
            tenant=self.tenant_a,
            enrollment=self.enrollment_a,
            class_section=section,
        )
        template = FeeTemplate.objects.create(
            tenant=self.tenant_a,
            name="월수강료",
            fee_type=FeeTemplate.FeeType.TUITION,
            amount=100000,
        )
        StudentFee.objects.create(
            tenant=self.tenant_a,
            student=self.student_a,
            fee_template=template,
            enrollment=self.enrollment_a,
        )
        invoice = StudentInvoice.objects.create(
            tenant=self.tenant_a,
            student=self.student_a,
            invoice_number=f"FEE-2026-05-{self.student_a.id}",
            billing_year=2026,
            billing_month=5,
            total_amount=100000,
            due_date=timezone.localdate() + timedelta(days=7),
        )
        InvoiceItem.objects.create(
            tenant=self.tenant_a,
            invoice=invoice,
            description="월수강료",
            amount=100000,
        )
        FeePayment.objects.create(
            tenant=self.tenant_a,
            invoice=invoice,
            student=self.student_a,
            amount=100000,
            payment_method="CASH",
            paid_at=timezone.now(),
        )

        result = permanently_delete_students(
            tenant=self.tenant_a,
            student_ids=[self.student_a.id],
        )

        self.assertEqual(result.deleted_count, 1)
        self.assertFalse(SectionAssignment.objects.filter(enrollment=self.enrollment_a).exists())
        self.assertFalse(StudentFee.objects.filter(student_id=self.student_a.id).exists())
        self.assertFalse(StudentInvoice.objects.filter(student_id=self.student_a.id).exists())
        self.assertFalse(InvoiceItem.objects.filter(invoice=invoice).exists())
        self.assertFalse(FeePayment.objects.filter(student_id=self.student_a.id).exists())
        self.assertFalse(Student.objects.filter(id=self.student_a.id).exists())

    def test_duplicate_cleanup_command_preserves_other_tenant_user_data(self):
        """중복 삭제 정리 명령도 영구삭제 SSOT를 사용해 cross-tenant User를 보존."""
        keep_user = User.objects.create_user(
            username="dup_keep", password="test1234", tenant=self.tenant_a, name="Keep",
        )
        remove_user = User.objects.create_user(
            username="dup_remove", password="test1234", tenant=self.tenant_a, name="Remove",
        )
        TenantMembership.ensure_active(tenant=self.tenant_a, user=keep_user, role="student")
        TenantMembership.ensure_active(tenant=self.tenant_a, user=remove_user, role="student")
        TenantMembership.ensure_active(tenant=self.tenant_b, user=remove_user, role="teacher")
        keep = Student.objects.create(
            tenant=self.tenant_a,
            user=keep_user,
            ps_number="_del_900_DUPKEEP",
            name="중복학생",
            phone="",
            parent_phone="01077778888",
            omr_code="77778888",
            deleted_at=timezone.now() - timedelta(days=2),
        )
        remove = Student.objects.create(
            tenant=self.tenant_a,
            user=remove_user,
            ps_number="_del_901_DUPREMOVE",
            name="중복학생",
            phone="",
            parent_phone="01077778888",
            omr_code="77778888",
            deleted_at=timezone.now() - timedelta(days=1),
        )
        Submission.objects.create(
            tenant=self.tenant_b,
            user=remove_user,
            enrollment_id=None,
            target_type="exam",
            target_id=77,
            source="omr_manual",
            status="done",
        )

        call_command("check_deleted_student_duplicates", "--fix", stdout=StringIO())

        self.assertTrue(Student.objects.filter(id=keep.id).exists())
        self.assertFalse(Student.objects.filter(id=remove.id).exists())
        self.assertTrue(User.objects.filter(id=remove_user.id).exists())
        self.assertTrue(TenantMembership.objects.filter(tenant=self.tenant_b, user=remove_user).exists())
        self.assertTrue(Submission.objects.filter(tenant=self.tenant_b, user=remove_user).exists())

    def test_purge_deleted_students_removes_fee_dependencies(self):
        """30일 purge 명령도 lifecycle 영구삭제 그래프를 사용한다."""
        old_user = User.objects.create_user(
            username="old_deleted", password="test1234", tenant=self.tenant_a, name="Old",
        )
        TenantMembership.ensure_active(tenant=self.tenant_a, user=old_user, role="student")
        old_student = Student.objects.create(
            tenant=self.tenant_a,
            user=old_user,
            ps_number="_del_902_OLD",
            name="오래된삭제",
            phone="",
            parent_phone="01066667777",
            omr_code="66667777",
            deleted_at=timezone.now() - timedelta(days=40),
        )
        template = FeeTemplate.objects.create(
            tenant=self.tenant_a,
            name="오래된삭제수강료",
            fee_type=FeeTemplate.FeeType.TUITION,
            amount=50000,
        )
        StudentFee.objects.create(
            tenant=self.tenant_a,
            student=old_student,
            fee_template=template,
        )

        call_command("purge_deleted_students", "--days", "30", stdout=StringIO())

        self.assertFalse(Student.objects.filter(id=old_student.id).exists())
        self.assertFalse(StudentFee.objects.filter(student_id=old_student.id).exists())
