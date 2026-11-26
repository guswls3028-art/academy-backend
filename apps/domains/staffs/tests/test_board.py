from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from unittest import skipUnless
from unittest.mock import patch
from uuid import uuid4

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import close_old_connections, connection, connections
from django.test import TestCase, TransactionTestCase
from django.urls import resolve
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.domains.staffs.models import StaffBoardPost, StaffBoardWriteReceipt
from apps.domains.staffs.services.board_writes import BoardWrite
from apps.support.inventory.storage_cleanup_dependencies import (
    process_pending_storage_cleanup_intents, uncertain_storage_write_settle_delay,
)


class StaffBoardTests(TestCase):
    base = "/api/v1/staffs/board/posts/"

    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(name="Board A", code="board-a")
        cls.other = Tenant.objects.create(name="Board B", code="board-b")
        cls.users = {}
        for role in ("owner", "admin", "staff", "teacher", "student", "parent"):
            user = get_user_model().objects.create(username=role, name=role, is_staff=role == "student")
            TenantMembership.objects.create(user=user, role=role, tenant=cls.tenant, is_active=True)
            cls.users[role] = user
        cls.moderator = get_user_model().objects.create(username="platform", is_staff=True, is_superuser=True)
        TenantMembership.objects.create(user=cls.users["teacher"], role="teacher", tenant=cls.other, is_active=True)

    def request(self, method, suffix="", data=None, user=None, tenant=None, fmt="json", key=None):
        url = self.base + suffix
        request = getattr(APIRequestFactory(), method)(url, data=data or {}, format=fmt, **({"HTTP_IDEMPOTENCY_KEY": key} if key else {}))
        request.tenant = tenant or self.tenant
        force_authenticate(request, user=user or self.users["teacher"])
        match = resolve(url.split("?")[0])
        return match.func(request, **match.kwargs)

    def post(self, category="free", key=None, **kwargs):
        return self.request("post", data={"category": category, "title": "공유", "body": "내용", **kwargs},
                            fmt="multipart" if "file" in kwargs else "json", key=key)

    def test_normal_journey_and_private_role_boundaries(self):
        posted = self.post()
        self.assertEqual(posted.status_code, 201, posted.data)
        detail = f"{posted.data['id']}/"
        self.assertTrue(posted.data["can_edit"])
        for role in ("owner", "admin", "staff", "teacher"):
            self.assertEqual(self.request("get", detail, user=self.users[role]).status_code, 200)
        for user in (self.users["student"], self.users["parent"], self.moderator):
            for method, path in (("get", ""), ("post", ""), ("get", detail),
                                 ("post", detail + "replies/"), ("get", detail + "attachment/")):
                self.assertEqual(self.request(method, path, user=user).status_code, 403)
        self.assertEqual(self.request("get", detail, tenant=self.other).status_code, 404)
        self.assertEqual(self.request("post", detail + "replies/", tenant=self.other,
                                      data={"body": "reply"}).status_code, 404)
        self.assertEqual(self.request("get", tenant=self.other).data["count"], 0)
        self.assertEqual(self.request("patch", detail, {"title": "수정"}, user=self.users["admin"]).status_code, 403)
        self.assertEqual(self.request("patch", detail, {"category": "notice"}).status_code, 400)
        self.assertEqual(self.request("patch", detail, {"title": "수정"}).status_code, 200)
        reply = self.request("post", detail + "replies/", {"body": "답변"}, user=self.users["staff"])
        self.assertEqual(reply.status_code, 201, reply.data)
        loaded = self.request("get", detail, user=self.users["staff"]).data
        self.assertEqual((loaded["title"], loaded["reply_count"], loaded["replies"][0]["body"]), ("수정", 1, "답변"))
        self.assertFalse(loaded["can_edit"])
        self.post("notice")
        page = self.request("get", "?category=free&page_size=1").data
        self.assertEqual(page["count"], 1)
        self.assertNotIn("body", page["results"][0])
        TenantMembership.objects.filter(tenant=self.tenant, user=self.users["teacher"]).update(is_active=False)
        self.assertEqual(self.request("get", detail).status_code, 403)

    def test_search_filters_all_pages_inside_authorized_tenant_and_category(self):
        def resource(**fields):
            values = {"tenant": self.tenant, "category": "resource", "title": "공유", "body": "본문"}
            return StaffBoardPost.objects.create(**{**values, **fields})

        title = resource(title="MathPlan schedule")
        body = resource(body="첨부는 MATHPLAN 문서입니다.")
        file = resource(attachment_name="mathplan_100%.pdf")
        resource(tenant=self.other, title="MathPlan")
        resource(category="notice", title="MathPlan")
        for _ in range(21):
            resource()

        path = "?category=resource"
        listing = self.request("get", path).data
        blank = self.request("get", path + "&q=%20%20").data
        self.assertEqual((listing["count"], listing["results"]), (blank["count"], blank["results"]))
        self.assertNotIn(title.pk, [row["id"] for row in listing["results"]])
        result = self.request("get", path + "&q=%20mathplan%20&page_size=2").data
        self.assertEqual(result["count"], 3)
        self.assertEqual([row["id"] for row in result["results"]], [file.pk, body.pk])
        self.assertNotIn("body", result["results"][1])
        next_page = self.request("get", path + "&q=mathplan&page=2&page_size=2").data
        self.assertEqual([row["id"] for row in next_page["results"]], [title.pk])
        self.assertEqual(self.request("get", path + "&q=%25").data["count"], 1)
        self.assertEqual(self.request("get", path + "&q=mathplan", tenant=self.other).data["count"], 1)
        for role in ("student", "parent"):
            self.assertEqual(self.request("get", path + "&q=mathplan", user=self.users[role]).status_code, 403)
        self.assertEqual(self.request("get", path + "&q=%20" + "a" * 100 + "%20").status_code, 200)
        too_long = self.request("get", path + "&q=" + "a" * 101)
        self.assertEqual(too_long.status_code, 400)
        self.assertEqual(too_long.data["q"], ["검색어는 100자 이하로 입력해 주세요."])
        malformed = self.request("get", path + "&q=math%00plan")
        self.assertEqual(malformed.status_code, 400)
        self.assertEqual(malformed.data["q"][0].code, "null_characters_not_allowed")

    @patch("apps.domains.staffs.views.board.get_object_bytes_r2_storage", return_value=b"private")
    @patch("apps.domains.staffs.views.board.put_small_storage_object")
    def test_private_attachment(self, put, read):
        def upload():
            return SimpleUploadedFile("자료.txt", b"private")
        self.assertEqual(self.post(file=upload()).status_code, 400)
        put.assert_not_called()
        key = str(uuid4())
        response = self.post("resource", file=upload(), key=key)
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["attachment_name"], "자료.txt")
        replay = self.post("resource", file=upload(), key=key)
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(replay.data["id"], response.data["id"])
        self.assertTrue(response.data["can_remove_attachment"])
        self.assertEqual(self.post("resource", file=SimpleUploadedFile("자료.txt", b"changed"), key=key).status_code, 409)
        put.assert_called_once()
        detail = f"{response.data['id']}/attachment/"
        self.assertEqual(self.request("get", detail, tenant=self.other).status_code, 404)
        read.assert_not_called()
        response = self.request("get", detail, user=self.users["staff"])
        self.assertEqual(response.content, b"private")
        self.assertEqual(response["Content-Type"], "application/octet-stream")
        self.assertIn("filename*", response["Content-Disposition"])
        read.return_value = None
        self.assertEqual(self.request("get", detail).status_code, 404)
        read.side_effect = OSError()
        self.assertEqual(self.request("get", detail).status_code, 503)
        self.assertEqual(self.request("delete", detail, user=self.users["staff"]).status_code, 403)
        self.assertEqual(self.request("delete", detail, tenant=self.other).status_code, 404)
        with patch(
            "apps.infrastructure.storage.r2.delete_object_r2_storage",
        ) as delete:
            # Patch must remain active when Django invokes the commit callback.
            with self.captureOnCommitCallbacks(execute=True):
                self.assertEqual(self.request("delete", detail, user=self.users["admin"]).status_code, 204)
            delete.assert_called_once_with(key=put.call_args.kwargs["key"])
        self.assertEqual(self.request("delete", detail).status_code, 204)
        self.assertEqual(self.request("get", detail).status_code, 404)
        self.assertEqual(self.post("resource", file=upload(), key=key).data["attachment_name"], "")

    def test_reply_ownership_moderation_and_deleted_replay(self):
        detail = f"{self.post().data['id']}/"
        token = str(uuid4())
        staff = self.users["staff"]
        created = self.request("post", detail + "replies/", {"body": "첫 댓글"}, user=staff, key=token)
        target = detail + f"replies/{created.data['id']}/"
        self.assertTrue(created.data["can_edit"] and created.data["can_delete"])
        other = self.request("get", detail).data["replies"][0]
        self.assertFalse(other["can_edit"] or other["can_delete"])
        self.assertEqual(self.request("patch", target, {"body": "다른 작성자"}).status_code, 403)
        self.assertEqual(self.request("delete", target).status_code, 403)
        self.assertEqual(self.request("delete", target, tenant=self.other).status_code, 404)
        wrong_post = f"{self.post().data['id']}/replies/{created.data['id']}/"
        self.assertEqual(self.request("delete", wrong_post, user=staff).status_code, 404)
        self.assertEqual(self.request("patch", target, {"body": " "}, user=staff).status_code, 400)
        self.assertEqual(self.request("patch", target, {"body": "정정"}, user=staff).status_code, 200)
        self.assertEqual(self.request("get", detail).data["replies"][0]["body"], "정정")
        self.assertEqual(self.request("delete", target, user=staff).status_code, 204)
        self.assertEqual(self.request("get", detail).data["reply_count"], 0)
        self.assertEqual(self.request("post", detail + "replies/", {"body": "첫 댓글"}, user=staff, key=token).status_code, 404)
        # Pre-migration comments and account deletions retain their display name only.
        legacy = StaffBoardPost.objects.get(pk=detail[:-1]).replies.create(author_name=staff.name, body="옛 댓글")
        target = detail + f"replies/{legacy.pk}/"
        self.assertFalse(self.request("get", detail, user=staff).data["replies"][0]["can_edit"])
        self.assertEqual(self.request("patch", target, {"body": "변경"}, user=staff).status_code, 403)
        self.assertEqual(self.request("delete", target, user=self.users["admin"]).status_code, 204)

    @patch("apps.domains.staffs.views.board.put_small_storage_object")
    def test_delete_post_removes_every_entry_and_detaches_storage_once(self, put):
        token = str(uuid4())
        def upload():
            return self.post("resource", file=SimpleUploadedFile("note.txt", b"note"), key=token)
        post = upload().data
        detail = f"{post['id']}/"
        self.request("post", detail + "replies/", {"body": "기록"}, user=self.users["staff"])
        self.assertTrue(post["can_delete"])
        self.assertFalse(self.request("get", detail, user=self.users["staff"]).data["can_delete"])
        self.assertEqual(self.request("delete", detail, user=self.users["staff"]).status_code, 403)
        self.assertEqual(self.request("delete", detail, tenant=self.other).status_code, 404)
        self.assertEqual(self.request("delete", detail, user=self.users["student"]).status_code, 403)
        with patch("apps.domains.staffs.views.board.schedule_detached_storage_cleanup") as cleanup:
            for _ in range(2):
                self.assertEqual(self.request("delete", detail, user=self.users["admin"]).status_code, 204)
            cleanup.assert_called_once_with(tenant_id=self.tenant.pk, object_keys=[put.call_args.kwargs["key"]])
        self.assertEqual(self.request("get", "").data["count"], 0)
        self.assertEqual(self.request("get", detail).status_code, 404)
        self.assertEqual(self.request("get", detail + "attachment/").status_code, 404)
        self.assertEqual(self.request("post", detail + "replies/", {"body": "늦은 댓글"}).status_code, 404)
        self.assertEqual(upload().status_code, 404)
        self.assertEqual(StaffBoardPost.objects.get(pk=post["id"]).replies.count(), 1)

    def test_replay_post_and_reply(self):
        key = str(uuid4())
        initial = self.post(key=key)
        replay = self.post(key=key)
        self.assertEqual((initial.status_code, replay.status_code), (201, 200))
        self.assertEqual(initial.data["id"], replay.data["id"])
        self.assertEqual(self.post(key=key, title="changed").status_code, 409)
        self.assertEqual(self.post(key="invalid").status_code, 400)
        detail = f"{initial.data['id']}/replies/"
        for expected in (201, 200):
            self.assertEqual(self.request("post", detail, {"body": "first"}, key=key).status_code, expected)
        self.assertEqual(self.request("post", detail, {"body": "changed"}, key=key).status_code, 409)
        self.assertEqual(self.request("post", detail, {"body": "second"}, user=self.users["staff"], key=key).status_code, 201)
        self.assertEqual(StaffBoardWriteReceipt.objects.count(), 3)
        self.assertEqual(StaffBoardPost.objects.get().replies.count(), 2)

    @patch("apps.infrastructure.storage.r2.delete_object_r2_storage")
    @patch("apps.domains.staffs.views.board.put_small_storage_object", side_effect=OSError)
    def test_unconfirmed_upload_leaves_recoverable_intent(self, put, delete):
        intent_model = apps.get_model("submissions", "SubmissionStorageCleanupIntent")
        response = self.post("resource", file=SimpleUploadedFile("file.txt", b"data"))
        self.assertEqual(response.status_code, 503)
        self.assertFalse(StaffBoardPost.objects.exists())
        intent = intent_model.objects.get(tenant=self.tenant, object_key=put.call_args.kwargs["key"])
        self.assertEqual(intent.status, "pending")
        process_pending_storage_cleanup_intents()
        delete.assert_not_called()
        intent_model.objects.filter(pk=intent.pk).update(
            updated_at=timezone.now() - uncertain_storage_write_settle_delay() - timedelta(seconds=1),
        )
        process_pending_storage_cleanup_intents()
        delete.assert_called_once_with(key=intent.object_key)
        intent.refresh_from_db()
        self.assertEqual(intent.status, "cleaned")

    def test_interruption_after_put_rolls_back_post_and_preserves_recovery(self):
        class WorkerInterrupted(BaseException):
            pass

        intent_model = apps.get_model("submissions", "SubmissionStorageCleanupIntent")

        def accepted_put(*, fileobj, key):
            self.assertTrue(intent_model.objects.filter(tenant=self.tenant, object_key=key, status="pending").exists())

        token = str(uuid4())
        with patch("apps.domains.staffs.views.board.put_small_storage_object", side_effect=accepted_put), patch.object(
            BoardWrite, "finish", side_effect=WorkerInterrupted,
        ), self.assertRaises(WorkerInterrupted):
            self.post("resource", file=SimpleUploadedFile("interrupted.txt", b"data"), key=token)
        self.assertFalse(StaffBoardPost.objects.exists())
        self.assertFalse(StaffBoardWriteReceipt.objects.exists())
        self.assertEqual(intent_model.objects.filter(tenant=self.tenant, status="pending").count(), 1)
        with patch("apps.domains.staffs.views.board.put_small_storage_object"):
            retry = self.post("resource", file=SimpleUploadedFile("interrupted.txt", b"data"), key=token)
        self.assertEqual(retry.status_code, 201)


@skipUnless(connection.vendor == "postgresql", "Requires independent transactional connections")
class StaffBoardConcurrentTests(TransactionTestCase):
    def test_overlapping_resource_submission_retains_one_file_and_post(self):
        tenant = Tenant.objects.create(name="Board race", code="board-race")
        user = get_user_model().objects.create(username="race-author")
        TenantMembership.objects.create(tenant=tenant, user=user, role="staff", is_active=True)
        token = str(uuid4())
        barrier = Barrier(2)
        reserve = BoardWrite.reserve

        def synchronize(write):
            barrier.wait(timeout=20)
            return reserve(write)

        def submit():
            close_old_connections()
            try:
                url = "/api/v1/staffs/board/posts/"
                request = APIRequestFactory().post(url, {
                    "category": "resource", "title": "자료", "body": "본문",
                    "file": SimpleUploadedFile("자료.txt", b"private"),
                }, format="multipart", HTTP_IDEMPOTENCY_KEY=token)
                request.tenant = tenant
                force_authenticate(request, user=user)
                response = resolve(url).func(request)
                return response.status_code, response.data["id"]
            finally:
                connections.close_all()

        with patch.object(BoardWrite, "reserve", synchronize), patch(
            "apps.domains.staffs.views.board.put_small_storage_object",
        ) as put, ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: submit(), range(2)))
        self.assertEqual(sorted(code for code, _ in results), [200, 201])
        self.assertEqual({pk for _, pk in results}, {StaffBoardPost.objects.get().pk})
        put.assert_called_once()
        self.assertEqual(StaffBoardWriteReceipt.objects.count(), 1)
        intent_model = apps.get_model("submissions", "SubmissionStorageCleanupIntent")
        self.assertFalse(intent_model.objects.filter(tenant=tenant).exists())
