from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.core.models.user import user_internal_username
from apps.domains.messaging.models import MessageTemplate
from apps.domains.messaging.views.send_views import SendMessageView
from apps.domains.messaging.views.template_views import MessageTemplateListCreateView
from apps.domains.students.models import Student


User = get_user_model()


class SendMessageViewTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = Tenant.objects.create(
            code="msg-send",
            name="Msg Send",
            is_active=True,
            messaging_sender="01012345678",
        )
        self.admin = User.objects.create_user(
            username="msg-send-owner",
            password="test1234",
            tenant=self.tenant,
            is_staff=True,
        )
        TenantMembership.ensure_active(tenant=self.tenant, user=self.admin, role="owner")

        student_user = User.objects.create_user(
            username=user_internal_username(self.tenant, "S001"),
            password="test1234",
            tenant=self.tenant,
            phone="01011112222",
            name="테스트학생",
        )
        self.student = Student.objects.create(
            tenant=self.tenant,
            user=student_user,
            ps_number="S001",
            name="테스트학생",
            phone="01011112222",
            parent_phone="01033334444",
            omr_code="11112222",
        )
        TenantMembership.ensure_active(tenant=self.tenant, user=student_user, role="student")

        MessageTemplate.objects.create(
            tenant=self.tenant,
            category="default",
            name="자유양식",
            subject="",
            body="#{공지내용}\n#{사이트링크}",
            solapi_template_id="FREEFORM-SID",
            solapi_status="APPROVED",
            is_system=True,
        )

    def test_student_direct_alimtalk_uses_approved_freeform_template(self):
        request = self.factory.post(
            "/api/v1/messaging/send/",
            data={
                "send_to": "student",
                "student_ids": [self.student.id],
                "raw_body": "직접 작성한 안내입니다.",
                "block_category": "default",
            },
            format="json",
        )
        force_authenticate(request, user=self.admin)
        request.user = self.admin
        request.tenant = self.tenant

        with (
            patch("apps.domains.messaging.services.get_tenant_site_url", return_value="https://example.test"),
            patch("apps.domains.messaging.services.enqueue_sms", return_value=True) as enqueue_sms,
        ):
            response = SendMessageView.as_view()(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["enqueued"], 1)
        enqueue_sms.assert_called_once()
        kwargs = enqueue_sms.call_args.kwargs
        self.assertEqual(kwargs["to"], "01011112222")
        self.assertEqual(kwargs["target_type"], "student")
        self.assertEqual(kwargs["target_id"], self.student.id)
        self.assertEqual(kwargs["template_id"], "FREEFORM-SID")
        replacements = {item["key"]: item["value"] for item in kwargs["alimtalk_replacements"]}
        self.assertEqual(replacements["공지내용"], "직접 작성한 안내입니다.")
        self.assertEqual(replacements["내용"], "직접 작성한 안내입니다.")
        self.assertEqual(replacements["선생님메모"], "직접 작성한 안내입니다.")

    def test_parent_direct_alimtalk_uses_parent_phone_and_target_type(self):
        request = self.factory.post(
            "/api/v1/messaging/send/",
            data={
                "send_to": "parent",
                "student_ids": [self.student.id],
                "raw_body": "학부모 안내입니다.",
                "block_category": "default",
            },
            format="json",
        )
        force_authenticate(request, user=self.admin)
        request.user = self.admin
        request.tenant = self.tenant

        with (
            patch("apps.domains.messaging.services.get_tenant_site_url", return_value="https://example.test"),
            patch("apps.domains.messaging.services.enqueue_sms", return_value=True) as enqueue_sms,
        ):
            response = SendMessageView.as_view()(request)

        self.assertEqual(response.status_code, 200)
        enqueue_sms.assert_called_once()
        kwargs = enqueue_sms.call_args.kwargs
        self.assertEqual(kwargs["to"], "01033334444")
        self.assertEqual(kwargs["target_type"], "parent")
        self.assertEqual(kwargs["target_id"], self.student.id)

    def test_student_direct_alimtalk_omits_deleted_and_cross_tenant_students(self):
        deleted_user = User.objects.create_user(
            username=user_internal_username(self.tenant, "S002"),
            password="test1234",
            tenant=self.tenant,
            phone="01022223333",
            name="삭제학생",
        )
        deleted_student = Student.objects.create(
            tenant=self.tenant,
            user=deleted_user,
            ps_number="S002",
            name="삭제학생",
            phone="01022223333",
            parent_phone="01044445555",
            omr_code="22223333",
            deleted_at=timezone.now(),
        )
        other_tenant = Tenant.objects.create(code="msg-send-other", name="Other", is_active=True)
        other_user = User.objects.create_user(
            username=user_internal_username(other_tenant, "S999"),
            password="test1234",
            tenant=other_tenant,
            phone="01099998888",
            name="타원생",
        )
        other_student = Student.objects.create(
            tenant=other_tenant,
            user=other_user,
            ps_number="S999",
            name="타원생",
            phone="01099998888",
            parent_phone="01077776666",
            omr_code="99998888",
        )
        request = self.factory.post(
            "/api/v1/messaging/send/",
            data={
                "send_to": "parent",
                "student_ids": [self.student.id, deleted_student.id, other_student.id, self.student.id],
                "raw_body": "선택 학생 안내입니다.",
                "block_category": "default",
            },
            format="json",
        )
        force_authenticate(request, user=self.admin)
        request.user = self.admin
        request.tenant = self.tenant

        with (
            patch("apps.domains.messaging.services.get_tenant_site_url", return_value="https://example.test"),
            patch("apps.domains.messaging.services.enqueue_sms", return_value=True) as enqueue_sms,
        ):
            response = SendMessageView.as_view()(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["enqueued"], 1)
        enqueue_sms.assert_called_once()
        kwargs = enqueue_sms.call_args.kwargs
        self.assertEqual(kwargs["target_id"], self.student.id)
        self.assertEqual(kwargs["to"], "01033334444")

    def test_direct_raw_body_requires_entry_category(self):
        request = self.factory.post(
            "/api/v1/messaging/send/",
            data={
                "send_to": "student",
                "student_ids": [self.student.id],
                "raw_body": "진입점 없이 직접 보내는 안내입니다.",
            },
            format="json",
        )
        force_authenticate(request, user=self.admin)
        request.user = self.admin
        request.tenant = self.tenant

        with patch("apps.domains.messaging.services.enqueue_sms", return_value=True) as enqueue_sms:
            response = SendMessageView.as_view()(request)

        self.assertEqual(response.status_code, 400)
        self.assertIn("block_category", response.data)
        enqueue_sms.assert_not_called()

    def test_staff_membership_cannot_send_manual_messages(self):
        staff_user = User.objects.create_user(
            username="msg-send-staff",
            password="test1234",
            tenant=self.tenant,
            is_staff=True,
        )
        TenantMembership.ensure_active(tenant=self.tenant, user=staff_user, role="staff")
        request = self.factory.post(
            "/api/v1/messaging/send/",
            data={
                "send_to": "student",
                "student_ids": [self.student.id],
                "raw_body": "직원이 직접 발송하는 안내입니다.",
                "block_category": "default",
            },
            format="json",
        )
        force_authenticate(request, user=staff_user)
        request.user = staff_user
        request.tenant = self.tenant

        with patch("apps.domains.messaging.services.enqueue_sms", return_value=True) as enqueue_sms:
            response = SendMessageView.as_view()(request)

        self.assertEqual(response.status_code, 403)
        enqueue_sms.assert_not_called()

    def test_student_template_category_is_saved_as_default(self):
        request = self.factory.post(
            "/api/v1/messaging/templates/",
            data={
                "category": "student",
                "name": "학생 선택 안내",
                "subject": "",
                "body": "#{학생이름} 안내입니다.",
            },
            format="json",
        )
        force_authenticate(request, user=self.admin)
        request.user = self.admin
        request.tenant = self.tenant

        response = MessageTemplateListCreateView.as_view()(request)

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["category"], "default")
        saved = MessageTemplate.objects.get(id=response.data["id"])
        self.assertEqual(saved.category, MessageTemplate.Category.DEFAULT)
