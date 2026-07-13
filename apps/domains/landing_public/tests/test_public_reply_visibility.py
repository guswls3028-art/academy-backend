from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.domains.landing_public.api.views.reply_views import PublicPostReplyViewSet
from apps.domains.landing_public.api.views.review_views import PublicReviewViewSet
from apps.domains.landing_public.models import PublicBoardPost, PublicPostReply, PublicReview
from apps.domains.students.test_support import create_student_fixture


User = get_user_model()


class LandingPublicReplyVisibilityTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = Tenant.objects.create(name="Landing", code="landing-public", is_active=True)
        self.owner = User.objects.create_user(
            username="landing-owner",
            password="pw1234",
            tenant=self.tenant,
            is_staff=True,
        )
        TenantMembership.ensure_active(tenant=self.tenant, user=self.owner, role="owner")
        self.member = User.objects.create_user(
            username="landing-member",
            password="pw1234",
            tenant=self.tenant,
        )
        TenantMembership.ensure_active(tenant=self.tenant, user=self.member, role="student")
        create_student_fixture(
            tenant=self.tenant,
            user=self.member,
            name="Member",
            ps_number="LANDING-REPLY-1",
        )

    def _reply_list(self, target: str):
        request = self.factory.get("/landing-public/replies/", {"target": target})
        request.tenant = self.tenant
        return PublicPostReplyViewSet.as_view({"get": "list"})(request)

    def _reply_create(self, payload: dict):
        request = self.factory.post("/landing-public/replies/", payload, format="json")
        request.tenant = self.tenant
        force_authenticate(request, user=self.member)
        return PublicPostReplyViewSet.as_view({"post": "create"})(request)

    def test_hidden_board_replies_are_not_listed_or_created_for_public_member(self):
        board = PublicBoardPost.objects.create(
            tenant=self.tenant,
            author=self.owner,
            author_display_name="Owner",
            author_role="owner",
            title="Hidden board",
            content="Hidden",
            status=PublicBoardPost.Status.HIDDEN,
            external_visible=False,
        )
        PublicPostReply.objects.create(
            tenant=self.tenant,
            target_kind=PublicPostReply.TargetKind.BOARD,
            target_id=board.id,
            author=self.owner,
            author_display_name="Owner",
            author_role="owner",
            content="reply",
        )

        list_response = self._reply_list(f"board:{board.id}")
        create_response = self._reply_create(
            {
                "target_kind": PublicPostReply.TargetKind.BOARD,
                "target_id": board.id,
                "content": "new reply",
            }
        )

        self.assertEqual(list_response.status_code, 404, list_response.data)
        self.assertEqual(create_response.status_code, 404, create_response.data)

    def test_pending_review_replies_are_not_listed_or_created_for_other_member(self):
        review = PublicReview.objects.create(
            tenant=self.tenant,
            author=self.owner,
            author_display_name="Owner",
            author_role="owner",
            rating=5,
            title="Pending review",
            content="Pending",
            status=PublicReview.Status.PENDING,
        )
        PublicPostReply.objects.create(
            tenant=self.tenant,
            target_kind=PublicPostReply.TargetKind.REVIEW,
            target_id=review.id,
            author=self.owner,
            author_display_name="Owner",
            author_role="owner",
            content="reply",
        )

        list_response = self._reply_list(f"review:{review.id}")
        create_response = self._reply_create(
            {
                "target_kind": PublicPostReply.TargetKind.REVIEW,
                "target_id": review.id,
                "content": "new reply",
            }
        )

        self.assertEqual(list_response.status_code, 404, list_response.data)
        self.assertEqual(create_response.status_code, 404, create_response.data)

    def test_author_editing_approved_review_resets_moderation(self):
        review = PublicReview.objects.create(
            tenant=self.tenant,
            author=self.member,
            author_display_name="Member",
            author_role="student",
            rating=5,
            title="Approved review",
            content="Original",
            status=PublicReview.Status.APPROVED,
            reviewed_by=self.owner,
        )
        request = self.factory.patch(
            f"/landing-public/reviews/{review.id}/",
            {"content": "Edited"},
            format="json",
        )
        request.tenant = self.tenant
        force_authenticate(request, user=self.member)

        response = PublicReviewViewSet.as_view({"patch": "partial_update"})(request, pk=review.id)

        self.assertEqual(response.status_code, 200, response.data)
        review.refresh_from_db()
        self.assertEqual(review.content, "Edited")
        self.assertEqual(review.status, PublicReview.Status.PENDING)
        self.assertIsNone(review.reviewed_by)
        self.assertIsNone(review.reviewed_at)
