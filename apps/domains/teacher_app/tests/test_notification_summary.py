from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models.tenant import Tenant
from apps.core.models.tenant_membership import TenantMembership
from apps.domains.community.models import PostEntity, PostReply
from apps.domains.students.models import Student
from apps.domains.teacher_app.views import NotificationSummaryView


User = get_user_model()


class TestTeacherNotificationSummary(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = Tenant.objects.create(name="TeacherApp", code="teacher_app_sum", is_active=True)
        self.staff = User.objects.create_user(
            username="teacher_app_staff",
            password="pw1234",
            tenant=self.tenant,
            name="선생님",
        )
        TenantMembership.ensure_active(tenant=self.tenant, user=self.staff, role="teacher")
        self.student_user = User.objects.create_user(
            username="teacher_app_student",
            password="pw1234",
            tenant=self.tenant,
            name="학생",
        )
        TenantMembership.ensure_active(tenant=self.tenant, user=self.student_user, role="student")
        self.student = Student.objects.create(
            tenant=self.tenant,
            user=self.student_user,
            ps_number="TAS001",
            omr_code="11112222",
            name="학생",
            phone="01011112222",
            parent_phone="01033334444",
        )

    def _call(self):
        request = self.factory.get("/api/v1/teacher-app/notifications/summary/")
        request.tenant = self.tenant
        force_authenticate(request, user=self.staff)
        return NotificationSummaryView.as_view()(request)

    def test_summary_counts_only_unanswered_student_requests(self):
        PostEntity.objects.create(
            tenant=self.tenant,
            post_type="qna",
            title="학생 질문",
            content="c",
            created_by=self.student,
            author_role="student",
            status="published",
        )
        answered_qna = PostEntity.objects.create(
            tenant=self.tenant,
            post_type="qna",
            title="답변 완료 질문",
            content="c",
            created_by=self.student,
            author_role="student",
            status="published",
        )
        PostReply.objects.create(
            tenant=self.tenant,
            post=answered_qna,
            content="답변",
            author_role="staff",
        )
        PostEntity.objects.create(
            tenant=self.tenant,
            post_type="counsel",
            title="학생 상담",
            content="c",
            created_by=self.student,
            author_role="student",
            status="published",
        )
        PostEntity.objects.create(
            tenant=self.tenant,
            post_type="counsel",
            title="내부 상담 메모",
            content="c",
            author_role="staff",
            category_label="teacher_internal_memo",
            status="published",
        )

        response = self._call()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["qna_pending"], 1)
        self.assertEqual(response.data["counsel_pending"], 1)
        self.assertEqual(response.data["total"], 2)
