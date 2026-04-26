"""첨부파일 업로드 ViewSet 통합 — MIME 차단/크기 한도/테넌트 격리 (R2 mock).

end-to-end 시나리오: PostViewSet.upload_attachments에 multipart 요청 → 검증 거부.
"""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models.tenant import Tenant
from apps.core.models.tenant_membership import TenantMembership
from apps.domains.community.models import PostEntity, PostAttachment
from apps.domains.community.api.views.post_views import PostViewSet

User = get_user_model()


class TestAttachmentViewIntegration(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = Tenant.objects.create(name="T", code="t1", is_active=True)
        self.staff = User.objects.create_user(
            username="t_adm", password="pw1234", tenant=self.tenant, name="Admin",
        )
        TenantMembership.ensure_active(tenant=self.tenant, user=self.staff, role="owner")
        self.post = PostEntity.objects.create(
            tenant=self.tenant, post_type="board", title="t", content="c",
            author_role="staff", author_display_name="Admin", status="published",
        )

    def _upload(self, files):
        request = self.factory.post(
            f"/api/v1/community/posts/{self.post.id}/attachments/",
            data={"files": files}, format="multipart",
        )
        force_authenticate(request, user=self.staff)
        request.tenant = self.tenant
        view = PostViewSet.as_view({"post": "upload_attachments"})
        return view(request, pk=self.post.id)

    def test_block_html_extension(self):
        """A-1: .html 확장자 차단."""
        f = SimpleUploadedFile("evil.html", b"<script>alert(1)</script>", content_type="text/html")
        resp = self._upload([f])
        self.assertEqual(resp.status_code, 400)
        self.assertIn("html", resp.data["detail"].lower())
        # 업로드 시도조차 안 했으니 DB record 없음
        self.assertEqual(PostAttachment.objects.filter(post=self.post).count(), 0)

    def test_block_svg_extension(self):
        f = SimpleUploadedFile("logo.svg", b"<svg></svg>", content_type="image/svg+xml")
        resp = self._upload([f])
        self.assertEqual(resp.status_code, 400)

    def test_block_executable(self):
        f = SimpleUploadedFile("malware.exe", b"MZ", content_type="application/octet-stream")
        resp = self._upload([f])
        self.assertEqual(resp.status_code, 400)

    def test_block_unknown_mime(self):
        """화이트리스트 외 MIME은 거부."""
        f = SimpleUploadedFile("file.xyz", b"data", content_type="application/x-evil")
        resp = self._upload([f])
        self.assertEqual(resp.status_code, 400)

    @patch("apps.infrastructure.storage.r2.upload_fileobj_to_r2_storage")
    def test_allowed_jpg_uploads(self, mock_upload):
        """A-1: 허용 MIME은 정상 업로드 (R2 mock)."""
        mock_upload.return_value = None
        f = SimpleUploadedFile("photo.jpg", b"\xff\xd8\xff\xe0jpeg", content_type="image/jpeg")
        resp = self._upload([f])
        self.assertEqual(resp.status_code, 201, f"resp: {resp.data}")
        self.assertEqual(PostAttachment.objects.filter(post=self.post).count(), 1)
        att = PostAttachment.objects.get(post=self.post)
        # A-3: 파일명 sanitize 검증 — 안전한 파일명은 그대로
        self.assertEqual(att.original_name, "photo.jpg")
        # R2 키에 tenant/post 경로 포함
        self.assertIn(f"tenants/{self.tenant.id}/community/posts/{self.post.id}/", att.r2_key)

    @patch("apps.infrastructure.storage.r2.upload_fileobj_to_r2_storage")
    def test_filename_path_traversal_sanitized(self, mock_upload):
        """A-3: 파일명에 ../ 포함 시 sanitize."""
        mock_upload.return_value = None
        f = SimpleUploadedFile("../../etc/passwd.pdf", b"%PDF-fake", content_type="application/pdf")
        resp = self._upload([f])
        self.assertEqual(resp.status_code, 201)
        att = PostAttachment.objects.get(post=self.post)
        # / 가 _로 치환되어야 함
        self.assertNotIn("/", att.original_name)
        self.assertNotIn("..", att.r2_key.replace(f"tenants/{self.tenant.id}/", ""))

    def test_oversized_file_rejected(self):
        """50MB 초과 거부."""
        big_content = b"x" * (51 * 1024 * 1024)
        f = SimpleUploadedFile("big.pdf", big_content, content_type="application/pdf")
        resp = self._upload([f])
        self.assertEqual(resp.status_code, 400)
        self.assertIn("50MB", resp.data["detail"])

    def test_too_many_files_rejected(self):
        """11개 이상 거부."""
        files = [
            SimpleUploadedFile(f"f{i}.pdf", b"%PDF-", content_type="application/pdf")
            for i in range(11)
        ]
        resp = self._upload(files)
        self.assertEqual(resp.status_code, 400)

    @patch("apps.infrastructure.storage.r2.upload_fileobj_to_r2_storage")
    def test_partial_block_aborts_all(self, mock_upload):
        """파일 한 개만 차단되어도 모두 거부 (전체 검증 후 업로드)."""
        mock_upload.return_value = None
        ok = SimpleUploadedFile("ok.pdf", b"%PDF-", content_type="application/pdf")
        bad = SimpleUploadedFile("evil.html", b"<x>", content_type="text/html")
        resp = self._upload([ok, bad])
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(PostAttachment.objects.filter(post=self.post).count(), 0)
        # 단 한 번도 R2 업로드 시도 안 함
        mock_upload.assert_not_called()


class TestAttachmentTenantIsolation(TestCase):
    """다른 테넌트의 post_id로 attachment upload 불가."""

    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant_a = Tenant.objects.create(name="A", code="testa", is_active=True)
        self.tenant_b = Tenant.objects.create(name="B", code="testb", is_active=True)
        self.staff_a = User.objects.create_user(
            username="ta_adm", password="pw1234", tenant=self.tenant_a, name="A",
        )
        TenantMembership.ensure_active(tenant=self.tenant_a, user=self.staff_a, role="owner")
        # B 테넌트의 글 — A의 staff가 B 글에 첨부 시도
        self.post_b = PostEntity.objects.create(
            tenant=self.tenant_b, post_type="board", title="b", content="c",
            author_role="staff", status="published",
        )

    def test_cross_tenant_attachment_denied(self):
        f = SimpleUploadedFile("ok.pdf", b"%PDF-", content_type="application/pdf")
        request = self.factory.post(
            f"/api/v1/community/posts/{self.post_b.id}/attachments/",
            data={"files": [f]}, format="multipart",
        )
        force_authenticate(request, user=self.staff_a)
        request.tenant = self.tenant_a  # A 테넌트로 요청
        view = PostViewSet.as_view({"post": "upload_attachments"})
        resp = view(request, pk=self.post_b.id)
        # B의 post는 A 테넌트로는 조회 불가 → 404
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(PostAttachment.objects.filter(post=self.post_b).count(), 0)
