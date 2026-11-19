from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import resolve
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.domains.staffs.models import StaffBoardPost


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

    def request(self, method, suffix="", data=None, user=None, tenant=None, fmt="json"):
        url = self.base + suffix
        request = getattr(APIRequestFactory(), method)(url, data=data or {}, format=fmt)
        request.tenant = tenant or self.tenant
        force_authenticate(request, user=user or self.users["teacher"])
        match = resolve(url.split("?")[0])
        return match.func(request, **match.kwargs)

    def post(self, category="free", **kwargs):
        return self.request("post", data={"category": category, "title": "공유", "body": "내용", **kwargs},
                            fmt="multipart" if "file" in kwargs else "json")

    def test_normal_journey_and_private_role_boundaries(self):
        posted = self.post()
        self.assertEqual(posted.status_code, 201, posted.data)
        detail = f"{posted.data['id']}/"
        self.assertTrue(posted.data["can_edit"])
        for role in ("owner", "admin", "staff", "teacher"):
            self.assertEqual(self.request("get", detail, user=self.users[role]).status_code, 200)
        for user in (self.users["student"], self.users["parent"], self.moderator):
            for method, path in (("get", ""), ("post", ""), ("get", detail),
                                 ("post", detail + "replies/"), ("get", detail + "download/")):
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

    @patch("apps.domains.staffs.views.board.generate_presigned_get_url_storage", return_value="https://example.invalid/private")
    @patch("apps.domains.staffs.views.board.upload_fileobj_to_r2_storage")
    def test_private_attachment(self, put, sign):
        def upload():
            return SimpleUploadedFile("자료.txt", b"private")
        self.assertEqual(self.post(file=upload()).status_code, 400)
        put.assert_not_called()
        response = self.post("resource", file=upload())
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["attachment_name"], "자료.txt")
        detail = f"{response.data['id']}/download/"
        self.assertEqual(self.request("get", detail, tenant=self.other).status_code, 404)
        sign.assert_not_called()
        response = self.request("get", detail, user=self.users["staff"])
        self.assertEqual(response.data, {"url": sign.return_value})
        key = put.call_args.kwargs["key"]
        self.assertTrue(key.startswith(f"tenants/{self.tenant.pk}/staff-board/"))
        self.assertEqual(sign.call_args.kwargs["key"], key)

    @patch("apps.domains.staffs.views.board.compensate_unattached_storage_object")
    @patch("apps.domains.staffs.views.board.upload_fileobj_to_r2_storage", side_effect=OSError)
    def test_unconfirmed_upload_rolls_back_and_compensates(self, put, compensate):
        response = self.post("resource", file=SimpleUploadedFile("file.txt", b"data"))
        self.assertEqual(response.status_code, 503)
        self.assertFalse(StaffBoardPost.objects.exists())
        compensate.assert_called_once_with(tenant_id=self.tenant.pk, key=put.call_args.kwargs["key"], uncertain_write=True)
