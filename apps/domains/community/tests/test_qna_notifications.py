from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.core.models.tenant import Tenant
from apps.core.models.tenant_membership import TenantMembership
from apps.domains.community.models import PostEntity, PostReply
from apps.domains.community.services.qna_notifications import (
    notify_qna_answered,
    notify_qna_created,
)
from apps.domains.messaging.models import MessageTemplate
from apps.domains.students.models import Student

User = get_user_model()


class QnaNotificationTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="QnA학원", code="qna_notice", is_active=True)
        self.freeform_template = MessageTemplate.objects.create(
            tenant=self.tenant,
            category=MessageTemplate.Category.COMMUNITY,
            name="커뮤니티 자유 알림",
            subject="",
            body="#{공지내용}",
            solapi_template_id="KA_QNA_FREEFORM",
            solapi_status="APPROVED",
        )
        self.teacher = User.objects.create_user(
            username="qna_teacher",
            password="pw1234",
            tenant=self.tenant,
            name="김선생",
            phone="01090001111",
        )
        # 2026-05-30: QnA 알림은 owner 만 수신. 학원장 directive.
        TenantMembership.ensure_active(tenant=self.tenant, user=self.teacher, role="owner")
        self.student_user = User.objects.create_user(
            username="qna_student",
            password="pw1234",
            tenant=self.tenant,
            name="홍길동",
        )
        TenantMembership.ensure_active(tenant=self.tenant, user=self.student_user, role="student")
        self.student = Student.objects.create(
            tenant=self.tenant,
            user=self.student_user,
            ps_number="QNA001",
            name="홍길동",
            phone="01011112222",
            parent_phone="01033334444",
            omr_code="11112222",
        )
        self.post = PostEntity.objects.create(
            tenant=self.tenant,
            post_type="qna",
            title="수열 질문",
            content="질문입니다",
            created_by=self.student,
            author_role="student",
            author_display_name="홍길동",
            category_label="수학",
            status="published",
        )

    @patch("apps.domains.messaging.services.enqueue_sms", return_value=True)
    def test_notify_qna_created_skips_external_alimtalk_without_approved_envelope(self, mock_enqueue):
        sent = notify_qna_created(self.post, actor_user=self.student_user)

        self.assertEqual(sent, 0)
        mock_enqueue.assert_not_called()

    @patch("apps.domains.messaging.services.enqueue_sms", return_value=True)
    def test_notify_qna_created_skips_e2e_marked_post(self, mock_enqueue):
        self.post.title = "[E2E] QnA 테스트 질문"
        self.post.save(update_fields=["title"])

        sent = notify_qna_created(self.post, actor_user=self.student_user)

        self.assertEqual(sent, 0)
        mock_enqueue.assert_not_called()

    @patch("apps.domains.messaging.services.enqueue_sms", return_value=True)
    def test_notify_qna_created_does_not_fallback_to_attendance_envelope(self, mock_enqueue):
        self.freeform_template.delete()

        sent = notify_qna_created(self.post, actor_user=self.student_user)

        self.assertEqual(sent, 0)
        mock_enqueue.assert_not_called()

    @patch("apps.domains.messaging.services.enqueue_sms", return_value=True)
    def test_notify_qna_answered_skips_external_alimtalk_without_approved_envelope(self, mock_enqueue):
        reply = PostReply.objects.create(
            tenant=self.tenant,
            post=self.post,
            content="답변입니다",
            author_role="staff",
            author_display_name="김선생",
        )

        sent = notify_qna_answered(self.post, reply, send_to="student", actor_user=self.teacher)

        self.assertEqual(sent, 0)
        mock_enqueue.assert_not_called()

    @patch("apps.domains.messaging.services.enqueue_sms", return_value=True)
    def test_notify_qna_answered_skips_e2e_marked_post(self, mock_enqueue):
        self.post.title = "[E2E-SAFE] 검증 질문"
        self.post.save(update_fields=["title"])
        reply = PostReply.objects.create(
            tenant=self.tenant,
            post=self.post,
            content="답변입니다",
            author_role="staff",
            author_display_name="김선생",
        )

        sent = notify_qna_answered(self.post, reply, send_to="student", actor_user=self.teacher)

        self.assertEqual(sent, 0)
        mock_enqueue.assert_not_called()

    @patch("apps.domains.messaging.services.enqueue_sms", return_value=True)
    def test_notify_qna_answered_does_not_fallback_to_attendance_envelope(self, mock_enqueue):
        self.freeform_template.delete()
        reply = PostReply.objects.create(
            tenant=self.tenant,
            post=self.post,
            content="답변입니다",
            author_role="staff",
            author_display_name="김선생",
        )

        sent = notify_qna_answered(self.post, reply, send_to="student", actor_user=self.teacher)

        self.assertEqual(sent, 0)
        mock_enqueue.assert_not_called()
