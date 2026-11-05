"""A tenant block prevents new content and edits; reads/removal retain their rules."""
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import force_authenticate

from apps.domains.community.api.views.admin_views import CommunityUserBlockView
from apps.domains.community.api.views.post_views import PostViewSet
from apps.domains.community.models import CommunityUserBlock, PostEntity, PostReply
from apps.domains.community.tests.test_p0_community_hardening import CommunityHardeningFixture


class TestBlockedCommunityMutations(CommunityHardeningFixture):
    def setUp(self):
        super().setUp()
        self.own_post = PostEntity.objects.create(
            tenant=self.tenant, post_type="board", title="Saved student post", content="Saved body",
            created_by=self.student, author_role="student", status="published",
        )
        self.own_reply = PostReply.objects.create(
            tenant=self.tenant, post=self.own_post, content="Saved reply",
            created_by=self.student, author_role="student",
        )
        CommunityUserBlock.objects.create(tenant=self.tenant, user=self.student_user, blocked_by=self.owner)

    def call(self, action, method="post", data=None, **kwargs):
        return PostViewSet.as_view({method: action})(
            self._request(method, self.student_user, "/api/v1/community/posts/", data),
            pk=self.own_post.id, **kwargs,
        )

    def test_existing_post_and_reply_cannot_be_edited_while_blocked(self):
        post = self.call("partial_update", "patch", {"title": "Changed after block"})
        reply = self.call("reply_detail", "patch", {"content": "Changed after block"}, reply_id=self.own_reply.id)
        self.assertEqual([post.status_code, reply.status_code], [403, 403])
        self.assertEqual(post.data["code"], "user_blocked")
        self.own_post.refresh_from_db()
        self.own_reply.refresh_from_db()
        self.assertEqual(self.own_post.title, "Saved student post")
        self.assertEqual(self.own_reply.content, "Saved reply")

    def test_reactions_and_reports_are_blocked(self):
        responses = [
            self.call("like"),
            self.call("reply_like", reply_id=self.own_reply.id),
            self.call("report_post", data={"reason": "spam", "detail": "Blocked report"}),
            self.call("report_reply", data={"reason": "spam", "detail": "Blocked report"}, reply_id=self.own_reply.id),
        ]
        self.assertEqual([response.status_code for response in responses], [403] * 4)
        self.assertEqual(self.own_post.likes.count(), 0)
        self.assertEqual(self.own_reply.likes.count(), 0)

    def test_blocked_user_can_still_remove_own_content(self):
        reply = self.call("reply_detail", "delete", reply_id=self.own_reply.id)
        post = self.call("destroy", "delete")
        self.assertEqual([reply.status_code, post.status_code], [204, 204])
        self.assertFalse(PostEntity.objects.filter(pk=self.own_post.id).exists())
        self.assertFalse(PostReply.objects.filter(pk=self.own_reply.id).exists())

    def test_blocked_upload_does_not_write_storage_or_metadata(self):
        request = self.factory.post(
            "/api/v1/community/posts/attachments/",
            {"files": [SimpleUploadedFile("qa.pdf", b"%PDF-1.4\nQA", content_type="application/pdf")]},
            format="multipart",
        )
        force_authenticate(request, user=self.student_user)
        request.tenant = self.tenant
        with patch("apps.infrastructure.storage.r2.upload_fileobj_to_r2_storage") as upload:
            response = PostViewSet.as_view({"post": "upload_attachments"})(request, pk=self.own_post.id)
        self.assertEqual(response.status_code, 403)
        upload.assert_not_called()
        self.assertFalse(self.own_post.attachments.exists())

    def test_read_then_unblock_restores_normal_save_and_reload(self):
        self.assertEqual(self.call("retrieve", "get").status_code, 200)
        unblocked = CommunityUserBlockView.as_view()(
            self._request("delete", self.owner, "/api/v1/community/admin/user-blocks/"),
            user_id=self.student_user.id,
        )
        self.assertEqual(unblocked.status_code, 200)
        self.assertEqual(unblocked.data["deleted"], 1)
        response = self.call("partial_update", "patch", {"title": "Saved after unblock"})
        self.assertEqual(response.status_code, 200)
        self.own_post.refresh_from_db()
        self.assertEqual(self.own_post.title, "Saved after unblock")
        reloaded = self.call("retrieve", "get")
        self.assertEqual(reloaded.data["title"], "Saved after unblock")

    def test_other_tenant_block_does_not_restrict_current_tenant(self):
        CommunityUserBlock.objects.filter(tenant=self.tenant, user=self.student_user).delete()
        CommunityUserBlock.objects.create(tenant=self.other_tenant, user=self.student_user)
        self.assertEqual(self.call("partial_update", "patch", {"title": "Current tenant"}).status_code, 200)
