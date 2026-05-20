from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models.tenant import Tenant
from apps.core.models.tenant_membership import TenantMembership
from apps.domains.community.api.views.admin_views import AdminPostViewSet
from apps.domains.community.api.views.post_views import PostViewSet
from apps.domains.community.models import (
    CommunityReport,
    PostAttachment,
    PostEntity,
    PostLike,
    PostMapping,
    PostReply,
    ScopeNode,
)
from apps.domains.enrollment.models import Enrollment
from apps.domains.lectures.models import Lecture
from apps.domains.parents.models import Parent
from apps.domains.students.models import Student

User = get_user_model()


class CommunityHardeningFixture(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = Tenant.objects.create(name="Community", code="comm_p0", is_active=True)
        self.other_tenant = Tenant.objects.create(name="Other", code="comm_p0_other", is_active=True)

        self.owner = User.objects.create_user(
            username="comm_owner", password="pw1234", tenant=self.tenant, name="Owner"
        )
        TenantMembership.ensure_active(tenant=self.tenant, user=self.owner, role="owner")

        self.teacher = User.objects.create_user(
            username="comm_teacher", password="pw1234", tenant=self.tenant, name="Teacher"
        )
        TenantMembership.ensure_active(tenant=self.tenant, user=self.teacher, role="teacher")

        self.student_user = User.objects.create_user(
            username="comm_student", password="pw1234", tenant=self.tenant, name="Student"
        )
        TenantMembership.ensure_active(tenant=self.tenant, user=self.student_user, role="student")
        self.student = Student.objects.create(
            tenant=self.tenant,
            user=self.student_user,
            ps_number="S001",
            name="Student",
            phone="01011112222",
            parent_phone="01033334444",
            omr_code="11112222",
        )

        self.parent_user = User.objects.create_user(
            username="comm_parent", password="pw1234", tenant=self.tenant, name="Parent"
        )
        TenantMembership.ensure_active(tenant=self.tenant, user=self.parent_user, role="parent")
        self.parent = Parent.objects.create(
            tenant=self.tenant,
            user=self.parent_user,
            name="Parent",
            phone="01033334444",
        )
        self.student.parent = self.parent
        self.student.save(update_fields=["parent"])

        self.visible_lecture = Lecture.objects.create(
            tenant=self.tenant,
            title="Visible Lecture",
            name="Visible Lecture",
            subject="math",
        )
        self.hidden_lecture = Lecture.objects.create(
            tenant=self.tenant,
            title="Hidden Lecture",
            name="Hidden Lecture",
            subject="math",
        )
        self.foreign_lecture = Lecture.objects.create(
            tenant=self.other_tenant,
            title="Foreign Lecture",
            name="Foreign Lecture",
            subject="math",
        )
        Enrollment.objects.create(
            tenant=self.tenant,
            student=self.student,
            lecture=self.visible_lecture,
            status="ACTIVE",
        )

        self.visible_node = ScopeNode.objects.create(
            tenant=self.tenant,
            level=ScopeNode.Level.COURSE,
            lecture=self.visible_lecture,
        )
        self.hidden_node = ScopeNode.objects.create(
            tenant=self.tenant,
            level=ScopeNode.Level.COURSE,
            lecture=self.hidden_lecture,
        )
        self.foreign_node = ScopeNode.objects.create(
            tenant=self.other_tenant,
            level=ScopeNode.Level.COURSE,
            lecture=self.foreign_lecture,
        )

        self.visible_post = PostEntity.objects.create(
            tenant=self.tenant,
            post_type="board",
            title="Visible board",
            content="visible",
            author_role="staff",
            status="published",
        )
        PostMapping.objects.create(post=self.visible_post, node=self.visible_node)

        self.hidden_post = PostEntity.objects.create(
            tenant=self.tenant,
            post_type="board",
            title="Hidden board",
            content="hidden",
            author_role="staff",
            status="published",
        )
        PostMapping.objects.create(post=self.hidden_post, node=self.hidden_node)
        self.hidden_reply = PostReply.objects.create(
            tenant=self.tenant,
            post=self.hidden_post,
            content="hidden reply",
            author_role="staff",
        )
        self.hidden_attachment = PostAttachment.objects.create(
            tenant=self.tenant,
            post=self.hidden_post,
            r2_key="tenants/1/community/posts/hidden/file.pdf",
            original_name="file.pdf",
            size_bytes=5,
            content_type="application/pdf",
        )

    def _request(self, method, user, path, data=None, format="json", **extra):
        request = getattr(self.factory, method)(path, data=data or {}, format=format, **extra)
        force_authenticate(request, user=user)
        request.tenant = self.tenant
        return request


class TestScopedPostMappingVisibility(CommunityHardeningFixture):
    def test_student_direct_actions_require_visible_post_mapping(self):
        retrieve = PostViewSet.as_view({"get": "retrieve"})
        response = retrieve(
            self._request("get", self.student_user, f"/api/v1/community/posts/{self.hidden_post.id}/"),
            pk=self.hidden_post.id,
        )
        self.assertEqual(response.status_code, 404)

        replies = PostViewSet.as_view({"get": "replies"})
        response = replies(
            self._request("get", self.student_user, f"/api/v1/community/posts/{self.hidden_post.id}/replies/"),
            pk=self.hidden_post.id,
        )
        self.assertEqual(response.status_code, 404)

        download = PostViewSet.as_view({"get": "download_attachment"})
        response = download(
            self._request(
                "get",
                self.student_user,
                f"/api/v1/community/posts/{self.hidden_post.id}/attachments/{self.hidden_attachment.id}/download/",
            ),
            pk=self.hidden_post.id,
            att_id=self.hidden_attachment.id,
        )
        self.assertEqual(response.status_code, 404)

        like = PostViewSet.as_view({"post": "like"})
        response = like(
            self._request("post", self.student_user, f"/api/v1/community/posts/{self.hidden_post.id}/like/"),
            pk=self.hidden_post.id,
        )
        self.assertEqual(response.status_code, 404)
        self.assertFalse(PostLike.objects.filter(post=self.hidden_post, user=self.student_user).exists())

        report = PostViewSet.as_view({"post": "report_post"})
        response = report(
            self._request(
                "post",
                self.student_user,
                f"/api/v1/community/posts/{self.hidden_post.id}/report/",
                {"reason": CommunityReport.REASON_OTHER},
            ),
            pk=self.hidden_post.id,
        )
        self.assertEqual(response.status_code, 404)
        self.assertFalse(CommunityReport.objects.filter(target_id=self.hidden_post.id).exists())

    def test_student_node_list_rejects_foreign_node_scope(self):
        view = PostViewSet.as_view({"get": "list"})
        response = view(
            self._request("get", self.student_user, f"/api/v1/community/posts/?node_id={self.hidden_node.id}")
        )
        self.assertEqual(response.status_code, 200)
        results = response.data.get("results", response.data) if isinstance(response.data, dict) else response.data
        self.assertEqual(len(results), 0)

    @patch("apps.infrastructure.storage.r2.generate_presigned_get_url_storage", return_value="https://example.test/file.pdf")
    def test_student_can_still_access_visible_mapped_post(self, _mock_url):
        visible_attachment = PostAttachment.objects.create(
            tenant=self.tenant,
            post=self.visible_post,
            r2_key="tenants/1/community/posts/visible/file.pdf",
            original_name="visible.pdf",
            size_bytes=5,
            content_type="application/pdf",
        )
        retrieve = PostViewSet.as_view({"get": "retrieve"})
        response = retrieve(
            self._request("get", self.student_user, f"/api/v1/community/posts/{self.visible_post.id}/"),
            pk=self.visible_post.id,
        )
        self.assertEqual(response.status_code, 200)

        download = PostViewSet.as_view({"get": "download_attachment"})
        response = download(
            self._request(
                "get",
                self.student_user,
                f"/api/v1/community/posts/{self.visible_post.id}/attachments/{visible_attachment.id}/download/",
            ),
            pk=self.visible_post.id,
            att_id=visible_attachment.id,
        )
        self.assertEqual(response.status_code, 200)


class TestParentCommunityReadOnly(CommunityHardeningFixture):
    def test_parent_board_list_and_counts_follow_child_enrollments(self):
        board = PostViewSet.as_view({"get": "board"})
        response = board(
            self._request("get", self.parent_user, "/api/v1/community/posts/board/?page_size=20")
        )

        self.assertEqual(response.status_code, 200)
        ids = {row["id"] for row in response.data["results"]}
        self.assertIn(self.visible_post.id, ids)
        self.assertNotIn(self.hidden_post.id, ids)

        counts = PostViewSet.as_view({"get": "counts"})
        response = counts(
            self._request("get", self.parent_user, "/api/v1/community/posts/counts/?post_type=board")
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["total"], 1)
        self.assertEqual(response.data["by_node_id"], {self.visible_node.id: 1})
        self.assertNotIn(self.hidden_node.id, response.data["by_node_id"])

    def test_limited_reader_counts_hide_private_post_types(self):
        other_student_user = User.objects.create_user(
            username="comm_other_student",
            password="pw1234",
            tenant=self.tenant,
            name="Other Student",
        )
        TenantMembership.ensure_active(
            tenant=self.tenant,
            user=other_student_user,
            role="student",
        )
        other_student = Student.objects.create(
            tenant=self.tenant,
            user=other_student_user,
            ps_number="S002",
            name="Other Student",
            phone="01055556666",
            parent_phone="01077778888",
            omr_code="22223333",
        )
        Enrollment.objects.create(
            tenant=self.tenant,
            student=other_student,
            lecture=self.visible_lecture,
            status="ACTIVE",
        )
        qna = PostEntity.objects.create(
            tenant=self.tenant,
            post_type="qna",
            title="Other qna",
            content="private",
            created_by=other_student,
            author_role="student",
            status="published",
        )
        PostMapping.objects.create(post=qna, node=self.visible_node)
        PostEntity.objects.create(
            tenant=self.tenant,
            post_type="counsel",
            title="Global counsel",
            content="private",
            created_by=other_student,
            author_role="student",
            status="published",
        )

        counts = PostViewSet.as_view({"get": "counts"})
        for user in (self.student_user, self.parent_user):
            for post_type in ("qna", "counsel"):
                response = counts(
                    self._request(
                        "get",
                        user,
                        f"/api/v1/community/posts/counts/?post_type={post_type}",
                    )
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.data["total"], 0)
                self.assertEqual(response.data["global_count"], 0)
                self.assertEqual(response.data["by_node_id"], {})

        owner_qna_response = counts(
            self._request("get", self.owner, "/api/v1/community/posts/counts/?post_type=qna")
        )
        self.assertEqual(owner_qna_response.status_code, 200)
        self.assertEqual(owner_qna_response.data["total"], 1)

    def test_parent_cannot_write_replies_or_attachments(self):
        reply = PostReply.objects.create(
            tenant=self.tenant,
            post=self.visible_post,
            content="student reply",
            created_by=self.student,
            author_role="student",
        )
        attachment = PostAttachment.objects.create(
            tenant=self.tenant,
            post=self.visible_post,
            r2_key="tenants/1/community/posts/visible/delete.pdf",
            original_name="delete.pdf",
            size_bytes=5,
            content_type="application/pdf",
        )

        cases = [
            (
                PostViewSet.as_view({"post": "replies"}),
                self._request(
                    "post",
                    self.parent_user,
                    f"/api/v1/community/posts/{self.visible_post.id}/replies/",
                    {"content": "parent reply"},
                ),
                {"pk": self.visible_post.id},
            ),
            (
                PostViewSet.as_view({"patch": "reply_detail"}),
                self._request(
                    "patch",
                    self.parent_user,
                    f"/api/v1/community/posts/{self.visible_post.id}/replies/{reply.id}/",
                    {"content": "edited"},
                ),
                {"pk": self.visible_post.id, "reply_id": reply.id},
            ),
            (
                PostViewSet.as_view({"delete": "reply_detail"}),
                self._request(
                    "delete",
                    self.parent_user,
                    f"/api/v1/community/posts/{self.visible_post.id}/replies/{reply.id}/",
                ),
                {"pk": self.visible_post.id, "reply_id": reply.id},
            ),
            (
                PostViewSet.as_view({"post": "upload_attachments"}),
                self._request(
                    "post",
                    self.parent_user,
                    f"/api/v1/community/posts/{self.visible_post.id}/attachments/",
                    {"files": [SimpleUploadedFile("parent.pdf", b"%PDF-", content_type="application/pdf")]},
                    format="multipart",
                ),
                {"pk": self.visible_post.id},
            ),
            (
                PostViewSet.as_view({"delete": "delete_attachment"}),
                self._request(
                    "delete",
                    self.parent_user,
                    f"/api/v1/community/posts/{self.visible_post.id}/attachments/{attachment.id}/",
                ),
                {"pk": self.visible_post.id, "att_id": attachment.id},
            ),
        ]
        for view, request, kwargs in cases:
            response = view(request, **kwargs)
            self.assertEqual(response.status_code, 403)
            self.assertEqual(response.data["code"], "parent_read_only")

        self.assertTrue(PostReply.objects.filter(id=reply.id, content="student reply").exists())
        self.assertTrue(PostAttachment.objects.filter(id=attachment.id).exists())
        self.assertEqual(PostAttachment.objects.filter(post=self.visible_post).count(), 1)

    def test_parent_cannot_like_or_report(self):
        like = PostViewSet.as_view({"post": "like"})
        response = like(
            self._request("post", self.parent_user, f"/api/v1/community/posts/{self.visible_post.id}/like/"),
            pk=self.visible_post.id,
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(PostLike.objects.filter(post=self.visible_post, user=self.parent_user).exists())

        report = PostViewSet.as_view({"post": "report_post"})
        response = report(
            self._request(
                "post",
                self.parent_user,
                f"/api/v1/community/posts/{self.visible_post.id}/report/",
                {"reason": CommunityReport.REASON_OTHER},
            ),
            pk=self.visible_post.id,
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(CommunityReport.objects.filter(reporter=self.parent_user).exists())


class TestCommunityNodeRemapping(CommunityHardeningFixture):
    def test_teacher_cannot_remap_nodes(self):
        view = PostViewSet.as_view({"patch": "update_nodes"})
        response = view(
            self._request(
                "patch",
                self.teacher,
                f"/api/v1/community/posts/{self.visible_post.id}/nodes/",
                {"node_ids": [self.hidden_node.id]},
            ),
            pk=self.visible_post.id,
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            list(PostMapping.objects.filter(post=self.visible_post).values_list("node_id", flat=True)),
            [self.visible_node.id],
        )

    def test_invalid_or_foreign_node_preserves_existing_mapping(self):
        view = PostViewSet.as_view({"patch": "update_nodes"})
        response = view(
            self._request(
                "patch",
                self.owner,
                f"/api/v1/community/posts/{self.visible_post.id}/nodes/",
                {"node_ids": [self.foreign_node.id]},
            ),
            pk=self.visible_post.id,
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            list(PostMapping.objects.filter(post=self.visible_post).values_list("node_id", flat=True)),
            [self.visible_node.id],
        )

    def test_owner_can_remap_to_valid_tenant_node(self):
        view = PostViewSet.as_view({"patch": "update_nodes"})
        response = view(
            self._request(
                "patch",
                self.owner,
                f"/api/v1/community/posts/{self.visible_post.id}/nodes/",
                {"node_ids": [self.hidden_node.id]},
            ),
            pk=self.visible_post.id,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            list(PostMapping.objects.filter(post=self.visible_post).values_list("node_id", flat=True)),
            [self.hidden_node.id],
        )


class TestCommunityAdminListLimits(CommunityHardeningFixture):
    def test_admin_page_size_is_capped(self):
        PostEntity.objects.bulk_create(
            [
                PostEntity(
                    tenant=self.tenant,
                    post_type="qna",
                    title=f"Post {idx}",
                    content="body",
                    author_role="staff",
                    status="published",
                )
                for idx in range(120)
            ]
        )
        view = AdminPostViewSet.as_view({"get": "list"})
        response = view(
            self._request(
                "get",
                self.owner,
                "/api/v1/community/admin/posts/?page_size=500",
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 122)
        self.assertEqual(len(response.data["results"]), 100)


class TestCommunityListQueryShape(CommunityHardeningFixture):
    def test_board_list_annotates_is_liked_without_per_row_like_queries(self):
        extra_posts = [
            PostEntity.objects.create(
                tenant=self.tenant,
                post_type="board",
                title=f"Post {idx}",
                content="body",
                author_role="staff",
                status="published",
            )
            for idx in range(12)
        ]
        for post in extra_posts[:3]:
            PostLike.objects.create(tenant=self.tenant, post=post, user=self.owner)

        view = PostViewSet.as_view({"get": "board"})
        request = self._request("get", self.owner, "/api/v1/community/posts/board/?page_size=20")

        with CaptureQueriesContext(connection) as captured:
            response = view(request)

        self.assertEqual(response.status_code, 200)
        self.assertGreaterEqual(len(response.data["results"]), 12)
        like_queries = [
            query["sql"]
            for query in captured.captured_queries
            if "community_post_like" in query["sql"]
        ]
        self.assertLessEqual(len(like_queries), 2)
        liked_flags = [row["is_liked"] for row in response.data["results"]]
        self.assertEqual(sum(1 for flag in liked_flags if flag), 3)
