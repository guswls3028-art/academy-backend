from unittest.mock import patch

from django.apps import apps
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import (
    LandingConsultRequest,
    OpsAuditLog,
    PlatformInboxIncidentState,
    Tenant,
    TenantMembership,
)
from apps.domains.community.api.views.platform_inbox_views import (
    PlatformInboxIncidentDetailView,
    PlatformInboxLeadDetailView,
    PlatformInboxListView,
    PlatformInboxReplyView,
)
from apps.domains.community.api.views.post_views import PostViewSet
from apps.domains.community.api.views.support_ticket_views import (
    SupportTicketListCreateView,
)
from apps.domains.community.models import PostAttachment, PostEntity, PostReply
from apps.domains.community.selectors import get_all_posts_for_tenant

User = get_user_model()


class PlatformSupportInboxTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.platform = Tenant.objects.create(
            name="Platform",
            code="support_platform",
            is_active=True,
        )
        self.tenant = Tenant.objects.create(
            name="Customer",
            code="support_customer",
            is_active=True,
        )
        self.other_tenant = Tenant.objects.create(
            name="Other",
            code="support_other",
            is_active=True,
        )
        self.platform_owner = User.objects.create_user(
            username="platform_owner",
            password="pw1234",
            tenant=self.platform,
            name="Platform Owner",
        )
        TenantMembership.ensure_active(
            tenant=self.platform,
            user=self.platform_owner,
            role="owner",
        )
        self.customer_staff = User.objects.create_user(
            username="customer_staff",
            password="pw1234",
            tenant=self.tenant,
            name="Customer Staff",
        )
        TenantMembership.ensure_active(
            tenant=self.tenant,
            user=self.customer_staff,
            role="teacher",
        )
        self.other_owner = User.objects.create_user(
            username="other_owner",
            password="pw1234",
            tenant=self.other_tenant,
            name="Other Owner",
        )
        TenantMembership.ensure_active(
            tenant=self.other_tenant,
            user=self.other_owner,
            role="owner",
        )

        self.support = PostEntity.objects.create(
            tenant=self.tenant,
            post_type="board",
            support_kind="bug",
            title="[BUG] 저장 오류",
            content="저장이 되지 않습니다.",
            author_display_name="Customer Staff",
            author_role="staff",
            status="published",
        )
        self.legacy_feedback = PostEntity.objects.create(
            tenant=self.tenant,
            post_type="board",
            title="[FEEDBACK] 검색 개선",
            content="검색 조건을 유지해 주세요.",
            author_display_name="Customer Staff",
            author_role="staff",
            status="published",
        )
        self.normal_post = PostEntity.objects.create(
            tenant=self.tenant,
            post_type="board",
            title="일반 공지",
            content="커뮤니티 공개 글",
            author_display_name="Customer Staff",
            author_role="staff",
            status="published",
        )
        self.other_support = PostEntity.objects.create(
            tenant=self.other_tenant,
            post_type="board",
            support_kind="feedback",
            title="[FB] 다른 학원 문의",
            content="다른 학원 내용",
            author_role="staff",
            status="published",
        )
        self.lead = LandingConsultRequest.objects.create(
            tenant=self.platform,
            name="문의자",
            phone="01012345678",
            interest="도입 상담",
            message="기능과 요금이 궁금합니다.",
            source="promo-contact",
            privacy_agreed=True,
            privacy_policy_version="2026-07",
        )
        LandingConsultRequest.objects.create(
            tenant=self.other_tenant,
            name="다른 문의자",
            phone="01000000000",
            source="promo-contact",
        )
        LandingConsultRequest.objects.create(
            tenant=self.platform,
            name="일반 상담",
            phone="01099999999",
            source="landing",
        )
        self.incident = OpsAuditLog.objects.create(
            actor_username="customer_staff",
            action="user_incident.manual",
            summary="Manual issue report",
            target_tenant=self.tenant,
            payload={
                "route": "/admin/results",
                "description": "화면이 멈췄습니다.",
                "screen_size": "1440x900",
            },
        )

    def _request(self, method, path, *, user, tenant, data=None, query=None):
        request = getattr(self.factory, method)(
            f"{path}{query or ''}",
            data={} if data is None else data,
            format="json",
        )
        force_authenticate(request, user=user)
        request.tenant = tenant
        return request

    def _platform_call(self, view, request, **kwargs):
        with override_settings(OWNER_TENANT_ID=self.platform.id):
            return view(request, **kwargs)

    def test_private_support_create_list_and_community_exclusion(self):
        create_response = SupportTicketListCreateView.as_view()(
            self._request(
                "post",
                "/api/v1/community/support/",
                user=self.customer_staff,
                tenant=self.tenant,
                data={
                    "type": "feedback",
                    "subject": "새 기능",
                    "content": "<script>alert(1)</script><p>요청 내용</p>",
                },
            )
        )
        self.assertEqual(create_response.status_code, 201, create_response.data)
        created = PostEntity.objects.get(pk=create_response.data["id"])
        self.assertEqual(created.support_kind, "feedback")
        self.assertTrue(created.title.startswith("[FB] "))
        self.assertNotIn("<script", created.content)

        list_response = SupportTicketListCreateView.as_view()(
            self._request(
                "get",
                "/api/v1/community/support/",
                user=self.customer_staff,
                tenant=self.tenant,
                query="?type=feedback",
            )
        )
        self.assertEqual(list_response.status_code, 200, list_response.data)
        listed_ids = {item["id"] for item in list_response.data["results"]}
        self.assertIn(created.id, listed_ids)
        self.assertIn(self.legacy_feedback.id, listed_ids)
        self.assertNotIn(self.other_support.id, listed_ids)

        board_response = PostViewSet.as_view({"get": "board"})(
            self._request(
                "get",
                "/api/v1/community/posts/board/",
                user=self.customer_staff,
                tenant=self.tenant,
            )
        )
        self.assertEqual(board_response.status_code, 200, board_response.data)
        board_ids = {item["id"] for item in board_response.data["results"]}
        self.assertEqual(board_ids, {self.normal_post.id})

    def test_new_support_ticket_queues_platform_push_once(self):
        response = SupportTicketListCreateView.as_view()(
            self._request(
                "post",
                "/api/v1/community/support/",
                user=self.customer_staff,
                tenant=self.tenant,
                data={
                    "type": "bug",
                    "subject": "알림 테스트",
                    "content": "신규 버그 문의입니다.",
                },
            )
        )

        self.assertEqual(response.status_code, 201, response.data)
        from apps.core.models import PlatformPushOutbox

        self.assertEqual(
            PlatformPushOutbox.objects.filter(
                kind="bug",
                item_id=response.data["id"],
            ).count(),
            1,
        )

    def test_support_create_and_platform_reply_retries_are_idempotent(self):
        create_data = {
            "type": "bug",
            "subject": "중복 방지",
            "content": "응답이 유실되어도 한 건이어야 합니다.",
            "idempotency_key": "support-create-retry-0001",
        }
        first_create = SupportTicketListCreateView.as_view()(
            self._request(
                "post",
                "/api/v1/community/support/",
                user=self.customer_staff,
                tenant=self.tenant,
                data=create_data,
            )
        )
        retry_create = SupportTicketListCreateView.as_view()(
            self._request(
                "post",
                "/api/v1/community/support/",
                user=self.customer_staff,
                tenant=self.tenant,
                data=create_data,
            )
        )
        self.assertEqual(first_create.status_code, 201, first_create.data)
        self.assertEqual(retry_create.status_code, 200, retry_create.data)
        self.assertEqual(first_create.data["id"], retry_create.data["id"])
        self.assertEqual(
            PostEntity.objects.filter(
                tenant=self.tenant,
                support_request_key="support-create-retry-0001",
            ).count(),
            1,
        )

        reply_data = {
            "content": "한 번만 저장되는 답변",
            "idempotency_key": "platform-reply-retry-0001",
        }
        first_reply = self._platform_call(
            PlatformInboxReplyView.as_view(),
            self._request(
                "post",
                "unused",
                user=self.platform_owner,
                tenant=self.platform,
                data=reply_data,
            ),
            post_id=first_create.data["id"],
        )
        retry_reply = self._platform_call(
            PlatformInboxReplyView.as_view(),
            self._request(
                "post",
                "unused",
                user=self.platform_owner,
                tenant=self.platform,
                data=reply_data,
            ),
            post_id=first_create.data["id"],
        )
        self.assertEqual(first_reply.status_code, 201, first_reply.data)
        self.assertEqual(retry_reply.status_code, 200, retry_reply.data)
        self.assertEqual(first_reply.data["id"], retry_reply.data["id"])
        self.assertEqual(
            PostReply.objects.filter(
                post_id=first_create.data["id"],
                platform_request_key="platform-reply-retry-0001",
            ).count(),
            1,
        )

        conflict = SupportTicketListCreateView.as_view()(
            self._request(
                "post",
                "/api/v1/community/support/",
                user=self.customer_staff,
                tenant=self.tenant,
                data={**create_data, "subject": "다른 내용"},
            )
        )
        self.assertEqual(conflict.status_code, 409)

    def test_platform_owner_can_see_unified_inbox_but_other_owner_cannot(self):
        self.lead.read_at = timezone.now()
        self.lead.save(update_fields=["read_at", "updated_at"])
        response = self._platform_call(
            PlatformInboxListView.as_view(),
            self._request(
                "get",
                "/api/v1/community/platform/inbox/",
                user=self.platform_owner,
                tenant=self.platform,
            ),
        )
        self.assertEqual(response.status_code, 200, response.data)
        sources_and_ids = {
            (item["source"], item["id"]) for item in response.data["results"]
        }
        self.assertIn(("support", self.support.id), sources_and_ids)
        self.assertIn(("support", self.legacy_feedback.id), sources_and_ids)
        self.assertIn(("support", self.other_support.id), sources_and_ids)
        self.assertIn(("lead", self.lead.id), sources_and_ids)
        self.assertIn(("incident", self.incident.id), sources_and_ids)
        self.assertEqual(response.data["summary"]["contacts"], 1)
        self.assertEqual(response.data["summary"]["open"], 5)
        lead_item = next(
            item
            for item in response.data["results"]
            if item["source"] == "lead" and item["id"] == self.lead.id
        )
        self.assertEqual(lead_item["status"], "open")

        denied = self._platform_call(
            PlatformInboxListView.as_view(),
            self._request(
                "get",
                "/api/v1/community/platform/inbox/",
                user=self.other_owner,
                tenant=self.other_tenant,
            ),
        )
        self.assertEqual(denied.status_code, 403)

    def test_platform_reply_is_support_only_and_controls_resolution(self):
        arbitrary = self._platform_call(
            PlatformInboxReplyView.as_view(),
            self._request(
                "post",
                f"/api/v1/community/platform/inbox/{self.normal_post.id}/replies/",
                user=self.platform_owner,
                tenant=self.platform,
                data={"content": "답변"},
            ),
            post_id=self.normal_post.id,
        )
        self.assertEqual(arbitrary.status_code, 404)

        reply_response = self._platform_call(
            PlatformInboxReplyView.as_view(),
            self._request(
                "post",
                f"/api/v1/community/platform/inbox/{self.support.id}/replies/",
                user=self.platform_owner,
                tenant=self.platform,
                data={"content": "<b>확인했습니다.</b>"},
            ),
            post_id=self.support.id,
        )
        self.assertEqual(reply_response.status_code, 201, reply_response.data)
        reply = PostReply.objects.get(pk=reply_response.data["id"])
        self.assertEqual(reply.tenant_id, self.tenant.id)
        self.assertEqual(reply.author_role, "platform_staff")
        self.assertFalse(reply_response.data["can_delete"])

        staff_reply = PostReply.objects.create(
            tenant=self.tenant,
            post=self.support,
            content="추가 질문",
            author_role="staff",
        )
        self.assertTrue(PostReply.objects.filter(pk=reply.id).exists())
        self.assertTrue(PostReply.objects.filter(pk=staff_reply.id).exists())

    def test_lead_and_incident_status_updates_preserve_original_records(self):
        lead_response = self._platform_call(
            PlatformInboxLeadDetailView.as_view(),
            self._request(
                "patch",
                f"/api/v1/community/platform/inbox/leads/{self.lead.id}/",
                user=self.platform_owner,
                tenant=self.platform,
                data={"status": "resolved", "admin_memo": "전화 상담 완료"},
            ),
            lead_id=self.lead.id,
        )
        self.assertEqual(lead_response.status_code, 200, lead_response.data)
        self.lead.refresh_from_db()
        self.assertIsNotNone(self.lead.read_at)
        self.assertIsNotNone(self.lead.resolved_at)
        self.assertEqual(self.lead.admin_memo, "전화 상담 완료")
        self.assertEqual(self.lead.message, "기능과 요금이 궁금합니다.")

        original_payload = dict(self.incident.payload)
        incident_response = self._platform_call(
            PlatformInboxIncidentDetailView.as_view(),
            self._request(
                "patch",
                f"/api/v1/community/platform/inbox/incidents/{self.incident.id}/",
                user=self.platform_owner,
                tenant=self.platform,
                data={"status": "resolved", "admin_memo": "재현 후 수정"},
            ),
            incident_id=self.incident.id,
        )
        self.assertEqual(incident_response.status_code, 200, incident_response.data)
        self.incident.refresh_from_db()
        self.assertEqual(self.incident.payload, original_payload)
        self.assertTrue(
            OpsAuditLog.objects.filter(
                action="inbox.incident_status",
                payload__incident_id=self.incident.id,
                payload__status="resolved",
            ).exists()
        )
        incident_state = PlatformInboxIncidentState.objects.get(
            incident=self.incident
        )
        self.assertEqual(incident_state.status, "resolved")
        self.assertEqual(incident_state.admin_memo, "재현 후 수정")
        self.assertEqual(incident_state.updated_by, self.platform_owner)

    def test_customer_followup_reopens_support_ticket(self):
        reply_response = self._platform_call(
            PlatformInboxReplyView.as_view(),
            self._request(
                "post",
                "unused",
                user=self.platform_owner,
                tenant=self.platform,
                data={"content": "첫 답변"},
            ),
            post_id=self.support.id,
        )
        self.assertEqual(reply_response.status_code, 201, reply_response.data)

        PostReply.objects.create(
            tenant=self.tenant,
            post=self.support,
            content="추가 질문이 있습니다.",
            author_display_name="Customer Staff",
            author_role="staff",
        )
        response = self._platform_call(
            PlatformInboxListView.as_view(),
            self._request(
                "get",
                "/api/v1/community/platform/inbox/",
                user=self.platform_owner,
                tenant=self.platform,
            ),
        )
        ticket = next(
            item
            for item in response.data["results"]
            if item["source"] == "support" and item["id"] == self.support.id
        )
        self.assertEqual(ticket["status"], "open")

    def test_legacy_prefix_only_applies_to_staff_board_posts(self):
        student_prefix = PostEntity.objects.create(
            tenant=self.tenant,
            post_type="board",
            title="[BUG] 학생 게시글",
            content="일반 게시판 글",
            author_role="student",
            status="published",
        )
        qna_prefix = PostEntity.objects.create(
            tenant=self.tenant,
            post_type="qna",
            title="[BUG] 수업 질문",
            content="일반 QnA",
            author_role="staff",
            status="published",
        )
        visible_ids = set(
            get_all_posts_for_tenant(self.tenant).values_list("id", flat=True)
        )
        self.assertIn(student_prefix.id, visible_ids)
        self.assertIn(qna_prefix.id, visible_ids)

        response = self._platform_call(
            PlatformInboxListView.as_view(),
            self._request(
                "get",
                "/api/v1/community/platform/inbox/",
                user=self.platform_owner,
                tenant=self.platform,
            ),
        )
        support_ids = {
            item["id"]
            for item in response.data["results"]
            if item["source"] == "support"
        }
        self.assertNotIn(student_prefix.id, support_ids)
        self.assertNotIn(qna_prefix.id, support_ids)

    def test_private_support_titles_do_not_leak_through_board_neighbors(self):
        student_user = User.objects.create_user(
            username="support_student",
            password="pw1234",
            tenant=self.tenant,
            name="Support Student",
        )
        TenantMembership.ensure_active(
            tenant=self.tenant,
            user=student_user,
            role="student",
        )
        student = apps.get_model("students", "Student").objects.create(
            tenant=self.tenant,
            user=student_user,
            ps_number="SUPPORT-S001",
            name="Support Student",
            phone="01022223333",
            parent_phone="01044445555",
            omr_code="22445533",
        )
        self.assertIsNotNone(student.pk)
        response = PostViewSet.as_view({"get": "neighbors"})(
            self._request(
                "get",
                f"/api/v1/community/posts/{self.normal_post.id}/neighbors/",
                user=student_user,
                tenant=self.tenant,
            ),
            pk=self.normal_post.id,
        )
        self.assertEqual(response.status_code, 200, response.data)
        neighbor_ids = {
            row["id"]
            for row in (response.data["prev"], response.data["next"])
            if row is not None
        }
        self.assertNotIn(self.support.id, neighbor_ids)
        self.assertNotIn(self.legacy_feedback.id, neighbor_ids)

    def test_platform_reply_and_support_attachment_cannot_be_mutated_by_tenant_staff(self):
        platform_reply = PostReply.objects.create(
            tenant=self.tenant,
            post=self.support,
            content="개발팀 답변",
            author_role="platform_staff",
        )
        patch_response = PostViewSet.as_view({"patch": "reply_detail"})(
            self._request(
                "patch",
                "unused",
                user=self.customer_staff,
                tenant=self.tenant,
                data={"content": "변조", "author_role": "staff"},
            ),
            pk=self.support.id,
            reply_id=platform_reply.id,
        )
        self.assertEqual(patch_response.status_code, 403)

        attachment = PostAttachment.objects.create(
            tenant=self.tenant,
            post=self.support,
            r2_key="support/private.png",
            original_name="private.png",
            size_bytes=10,
            content_type="image/png",
        )
        delete_attachment = PostViewSet.as_view({"delete": "delete_attachment"})(
            self._request(
                "delete",
                "unused",
                user=self.customer_staff,
                tenant=self.tenant,
            ),
            pk=self.support.id,
            att_id=attachment.id,
        )
        self.assertEqual(delete_attachment.status_code, 403)
        self.assertTrue(PostAttachment.objects.filter(pk=attachment.id).exists())

    def test_non_object_payloads_return_bad_request(self):
        support_response = SupportTicketListCreateView.as_view()(
            self._request(
                "post",
                "/api/v1/community/support/",
                user=self.customer_staff,
                tenant=self.tenant,
                data=[],
            )
        )
        self.assertEqual(support_response.status_code, 400)

        reply_response = self._platform_call(
            PlatformInboxReplyView.as_view(),
            self._request(
                "post",
                "unused",
                user=self.platform_owner,
                tenant=self.platform,
                data=[],
            ),
            post_id=self.support.id,
        )
        self.assertEqual(reply_response.status_code, 400)

    def test_old_open_lead_is_not_hidden_by_many_newer_resolved_leads(self):
        resolved_at = timezone.now()
        LandingConsultRequest.objects.bulk_create(
            [
                LandingConsultRequest(
                    tenant=self.platform,
                    name=f"완료 문의 {index}",
                    phone=f"010{index:08d}",
                    interest="완료된 도입 문의",
                    source="promo-contact",
                    resolved_at=resolved_at,
                )
                for index in range(1001)
            ]
        )
        response = self._platform_call(
            PlatformInboxListView.as_view(),
            self._request(
                "get",
                "/api/v1/community/platform/inbox/",
                user=self.platform_owner,
                tenant=self.platform,
                query="?type=contact&status=all&page=1&page_size=1",
            ),
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["count"], 1002)
        self.assertEqual(response.data["results"][0]["id"], self.lead.id)
        self.assertEqual(response.data["results"][0]["status"], "open")
