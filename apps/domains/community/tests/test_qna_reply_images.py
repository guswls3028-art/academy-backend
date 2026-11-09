from io import BytesIO
from unittest.mock import patch

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import resolve
from PIL import Image
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.domains.community.api.views.post_views import PostViewSet
from apps.domains.community.models import PostAttachment, PostEntity, PostReply, PostReplyAttachment

Student = apps.get_model("students", "Student")
SubmissionStorageCleanupIntent = apps.get_model("submissions", "SubmissionStorageCleanupIntent")


class QnaReplyImageTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = Tenant.objects.create(name="QA QnA", code="qa-qna-reply")
        self.staff = self.user("qa-teacher", "teacher")
        self.student_user = self.user("qa-student", "student")
        self.student = Student.objects.create(
            tenant=self.tenant, user=self.student_user, name="학생", ps_number="S01",
        )
        self.post = PostEntity.objects.create(
            tenant=self.tenant, post_type="qna", title="질문", content="풀이가 궁금합니다.",
            created_by=self.student, author_role="student",
        )
        self.replies_path = f"/api/v1/community/posts/{self.post.id}/replies/"
        self.photo_path = f"{self.replies_path}with-images/"

    def user(self, username, role, tenant=None):
        tenant = tenant or self.tenant
        user = get_user_model().objects.create_user(username=username, tenant=tenant)
        TenantMembership.ensure_active(tenant=tenant, user=user, role=role)
        return user

    def image(self, name="풀이.png"):
        buffer = BytesIO()
        Image.new("RGB", (16, 16), "red").save(buffer, format="PNG")
        return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/png")

    def request(self, method, action, path, user=None, data=None, tenant=None, **kwargs):
        request = getattr(self.factory, method)(path, data=data, format="multipart" if method == "post" else "json")
        force_authenticate(request, user or self.staff)
        request.tenant = tenant or self.tenant
        return PostViewSet.as_view({method: action})(request, pk=self.post.id, **kwargs)

    def post_photos(self, content="", files=None, user=None, tenant=None):
        return self.request("post", "photo_reply", self.photo_path, user, {
            "content": content,
            "files": files if files is not None else [self.image()],
            "idempotency_key": "qa-reply-photo-attempt-1",
        }, tenant)

    @patch("apps.domains.community.api.views.post_views.get_reply_event_notifier")
    @patch("apps.domains.community.services.attachment_urls.r2.generate_presigned_get_url_storage", return_value="https://example.invalid/image")
    @patch("apps.domains.community.api.views._reply_images.upload_fileobj_to_r2_storage")
    def test_photo_only_reply_persists_and_is_idempotent(self, upload, _sign, notification):
        self.assertEqual(resolve(self.photo_path).func.actions["post"], "photo_reply")

        first = self.post_photos()
        self.assertEqual(first.status_code, 201, first.data)
        self.assertEqual(first.data["content"], "")
        photo = first.data["attachments"][0]
        self.assertEqual(photo["original_name"], "풀이.png")
        self.assertEqual(photo["download_url"], "https://example.invalid/image")
        self.assertEqual(photo["content_type"], "image/png")
        self.assertEqual(self.post.attachments.count(), 0)

        replay = self.post_photos()
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(replay.data["id"], first.data["id"])
        self.assertEqual(self.post_photos(content="다른 답변").status_code, 409)
        upload.assert_called_once()
        notification.return_value.assert_called_once()

        replies = self.request("get", "replies", self.replies_path, self.student_user)
        self.assertEqual(replies.status_code, 200)
        self.assertEqual(replies.data[0]["attachments"], [photo])

        stranger = self.user("qa-stranger", "student")
        Student.objects.create(tenant=self.tenant, user=stranger, name="다른 학생", ps_number="S02")
        self.assertEqual(self.request("get", "replies", self.replies_path, stranger).status_code, 404)
        self.assertEqual(self.post_photos(user=self.student_user).status_code, 403)
        other_tenant = Tenant.objects.create(name="Other", code="qa-qna-other")
        other_staff = self.user("qa-other-teacher", "teacher", other_tenant)
        self.assertEqual(self.post_photos(user=other_staff, tenant=other_tenant).status_code, 404)

        photo_key = PostReplyAttachment.objects.get(pk=photo["id"]).r2_key
        question_file = PostAttachment.objects.create(
            tenant=self.tenant, post=self.post,
            r2_key=f"tenants/{self.tenant.id}/community/posts/{self.post.id}/question.png",
            original_name="question.png", content_type="image/png",
        )
        deleted = self.request("delete", "destroy", f"/api/v1/community/posts/{self.post.id}/")
        self.assertEqual(deleted.status_code, 502)
        self.assertEqual(deleted.data["deleted"]["attachments"], 2)
        self.assertFalse(PostReplyAttachment.objects.filter(pk=photo["id"]).exists())
        self.assertEqual(set(SubmissionStorageCleanupIntent.objects.filter(
            tenant=self.tenant,
        ).values_list("object_key", flat=True)), {photo_key, question_file.r2_key})

    @patch("apps.domains.community.api.views._reply_images.upload_fileobj_to_r2_storage")
    def test_corrupt_file_never_creates_a_reply(self, upload):
        bad = SimpleUploadedFile("corrupt.png", b"not a photo", content_type="image/png")
        result = self.post_photos(files=[self.image(), bad])
        self.assertEqual(result.status_code, 400)
        upload.assert_not_called()
        self.assertFalse(PostReply.objects.filter(post=self.post).exists())

    @patch("apps.domains.community.api.views.post_views.get_reply_event_notifier")
    @patch("apps.domains.community.services.attachment_urls.r2.generate_presigned_get_url_storage", return_value="https://example.invalid/image")
    @patch("apps.domains.community.api.views._reply_images.upload_fileobj_to_r2_storage")
    def test_partial_upload_rolls_back_with_exact_cleanup_and_retry(self, upload, _sign, notification):
        def files():
            return [self.image("selected.png"), self.image("pasted.png")]

        upload.side_effect = [None, OSError("R2 request failed")]
        failed = self.post_photos(content="풀이를 확인해 주세요.", files=files())
        self.assertEqual(failed.status_code, 503)
        self.assertFalse(PostReply.objects.filter(post=self.post).exists())
        self.assertFalse(PostReplyAttachment.objects.exists())
        notification.assert_not_called()
        attempted = {call.kwargs["key"] for call in upload.call_args_list}
        self.assertEqual(set(SubmissionStorageCleanupIntent.objects.filter(
            tenant=self.tenant,
        ).values_list("object_key", flat=True)), attempted)

        upload.side_effect = None
        retry = self.post_photos(content="풀이를 확인해 주세요.", files=files())
        self.assertEqual(retry.status_code, 201, retry.data)
        self.assertEqual(len(retry.data["attachments"]), 2)
        reply = PostReply.objects.get(post=self.post)
        self.assertEqual(set(reply.attachments.values_list("r2_key", flat=True)), attempted)
        notification.return_value.assert_called_once()

        deleted = self.request("delete", "reply_detail", f"{self.replies_path}{reply.id}/", reply_id=reply.id)
        self.assertEqual(deleted.status_code, 502)
        self.assertEqual(deleted.data["code"], "community_storage_cleanup_pending")
        self.assertEqual(deleted.data["deleted"]["attachments"], 2)
        self.assertFalse(PostReply.objects.filter(post=self.post).exists())
        self.assertFalse(PostReplyAttachment.objects.exists())
