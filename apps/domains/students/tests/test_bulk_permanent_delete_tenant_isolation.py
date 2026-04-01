# PATH: apps/domains/students/tests/test_bulk_permanent_delete_tenant_isolation.py
"""
크로스테넌트 보호 증명 테스트 — bulk_permanent_delete

시나리오: User X가 Tenant A(학생), Tenant B(teacher Membership + Submission).
  Tenant A에서 영구삭제 시 Tenant B 데이터 보존 증명.
"""
from django.test import TestCase
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models.tenant import Tenant
from apps.core.models.tenant_membership import TenantMembership
from apps.domains.students.models import Student
from apps.domains.lectures.models import Lecture
from apps.domains.enrollment.models import Enrollment
from apps.domains.submissions.models import Submission
from apps.domains.students.views import StudentViewSet

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

    def test_user_deleted_when_orphaned(self):
        """멤버십이 전부 삭제되면 User도 삭제."""
        TenantMembership.objects.filter(tenant=self.tenant_b, user=self.user_x).delete()
        Submission.objects.filter(tenant=self.tenant_b, user=self.user_x).delete()

        resp = self._call(self.tenant_a, self.admin_a, [self.student_a.id])
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(User.objects.filter(id=self.user_x.id).exists(),
                         "orphan User는 삭제되어야 함")

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
