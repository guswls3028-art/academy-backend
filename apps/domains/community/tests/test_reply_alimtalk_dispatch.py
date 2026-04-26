"""Staff가 학생 QnA/상담에 답변 등록 시 알림톡 트리거가 호출되는지 검증."""
from unittest.mock import patch

from django.test import TestCase
from django.contrib.auth import get_user_model
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models.tenant import Tenant
from apps.core.models.tenant_membership import TenantMembership
from apps.domains.students.models import Student
from apps.domains.community.models import PostEntity
from apps.domains.community.api.views.post_views import PostViewSet

User = get_user_model()


class TestReplyAlimtalkDispatch(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = Tenant.objects.create(name="T", code="t1", is_active=True)
        self.staff = User.objects.create_user(
            username="t_adm", password="pw1234", tenant=self.tenant, name="원장",
        )
        TenantMembership.ensure_active(tenant=self.tenant, user=self.staff, role="owner")

        self.student_user = User.objects.create_user(
            username="t_stu", password="pw1234", tenant=self.tenant, name="홍길동",
        )
        TenantMembership.ensure_active(tenant=self.tenant, user=self.student_user, role="student")
        self.student = Student.objects.create(
            tenant=self.tenant, user=self.student_user,
            ps_number="S001", name="홍길동",
            phone="01011112222", parent_phone="01033334444", omr_code="11112222",
        )
        self.qna = PostEntity.objects.create(
            tenant=self.tenant, post_type="qna",
            title="함수 미분 질문", content="c",
            created_by=self.student, author_role="student",
            author_display_name="홍길동", status="published",
            category_label="수학",
        )
        self.counsel = PostEntity.objects.create(
            tenant=self.tenant, post_type="counsel",
            title="진로 상담 신청", content="c",
            created_by=self.student, author_role="student",
            author_display_name="홍길동", status="published",
            category_label="진로 상담",
        )
        self.board = PostEntity.objects.create(
            tenant=self.tenant, post_type="board",
            title="공지", content="c",
            created_by=None, author_role="staff",
            author_display_name="관리자", status="published",
        )

    def _post_reply(self, post):
        request = self.factory.post(
            f"/api/v1/community/posts/{post.id}/replies/",
            data={"content": "답변 본문"}, format="json",
        )
        request.tenant = self.tenant
        force_authenticate(request, user=self.staff)
        view = PostViewSet.as_view({"post": "replies"})
        return view(request, pk=post.id)

    @patch("apps.support.messaging.services.send_event_notification")
    def test_qna_reply_dispatches_qna_answered_to_student(self, mock_send):
        resp = self._post_reply(self.qna)
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(mock_send.call_count, 1)
        kwargs = mock_send.call_args.kwargs
        self.assertEqual(kwargs["trigger"], "qna_answered")
        self.assertEqual(kwargs["send_to"], "student")
        self.assertEqual(kwargs["student"], self.student)
        self.assertEqual(kwargs["context"]["강의명"], "수학")
        self.assertEqual(kwargs["context"]["차시명"], "함수 미분 질문")

    @patch("apps.support.messaging.services.send_event_notification")
    def test_counsel_reply_dispatches_to_student_and_parent(self, mock_send):
        resp = self._post_reply(self.counsel)
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertEqual(mock_send.call_count, 2)
        send_to_values = {c.kwargs["send_to"] for c in mock_send.call_args_list}
        self.assertEqual(send_to_values, {"student", "parent"})
        for call in mock_send.call_args_list:
            self.assertEqual(call.kwargs["trigger"], "counsel_answered")
            self.assertEqual(call.kwargs["context"]["강의명"], "진로 상담")

    @patch("apps.support.messaging.services.send_event_notification")
    def test_board_reply_does_not_dispatch(self, mock_send):
        resp = self._post_reply(self.board)
        self.assertEqual(resp.status_code, 201, resp.data)
        mock_send.assert_not_called()

    @patch("apps.support.messaging.services.send_event_notification")
    def test_dispatch_failure_does_not_break_reply(self, mock_send):
        mock_send.side_effect = RuntimeError("solapi down")
        resp = self._post_reply(self.qna)
        self.assertEqual(resp.status_code, 201, "알림톡 실패가 답변 등록을 막아선 안 됨")
