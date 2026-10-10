import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from apps.core.models import Tenant, TenantMembership
from apps.domains.inventory.models import InventoryFile, InventoryFolder
from apps.infrastructure.storage.r2 import generate_presigned_get_url_storage


class InventoryDownloadDispositionTests(SimpleTestCase):
    @patch("apps.infrastructure.storage.r2._storage_bucket", return_value="test-storage")
    @patch("apps.infrastructure.storage.r2._get_s3_client")
    def test_unicode_original_filename_and_header_controls(self, client, bucket):
        generate_presigned_get_url_storage(key="owned-key", filename='qa-한글"\r\n\\.HWP', content_type="application/x-hwp")
        params = client.return_value.generate_presigned_url.call_args.kwargs["Params"]
        self.assertEqual(params["ResponseContentDisposition"],
                         'attachment; filename="download"; filename*=UTF-8\'\'qa-%ED%95%9C%EA%B8%80_.HWP')
        self.assertEqual(params["ResponseContentType"], "application/x-hwp")
        generate_presigned_get_url_storage(key="owned-key")
        self.assertEqual(client.return_value.generate_presigned_url.call_args.kwargs["Params"],
                         {"Bucket": "test-storage", "Key": "owned-key"})


class InventoryInputContractTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(code="qa-inventory-input", name="Input QA")
        cls.user = get_user_model().objects.create_user(username="qa-inventory-input", tenant=cls.tenant)
        TenantMembership.ensure_active(tenant=cls.tenant, user=cls.user, role="teacher")
        cls.folder = InventoryFolder.objects.create(tenant=cls.tenant, scope="admin", name="Original")
        cls.file = InventoryFile.objects.create(
            tenant=cls.tenant, scope="admin", display_name="Original", original_name="qa.pdf",
            r2_key=f"tenants/{cls.tenant.pk}/qa.pdf", description="Original description",
        )

    def setUp(self):
        token = AccessToken.for_user(self.user)
        token["tenant_id"] = self.tenant.pk
        token["token_version"] = self.user.token_version or 0
        self.client = APIClient(HTTP_HOST="api.hakwonplus.com", HTTP_X_TENANT_CODE=self.tenant.code)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        self.client.raise_request_exception = False

    def call(self, endpoint, body, method="post"):
        return getattr(self.client, method)(
            f"/api/v1/storage/inventory/{endpoint}", json.dumps(body), content_type="application/json",
        )

    def test_non_object_json_never_mutates_or_crashes(self):
        endpoints = [("folders/", "post"), ("move/", "post"), ("presign/", "post"),
                     (f"folders/{self.folder.pk}/", "patch"), (f"files/{self.file.pk}/", "patch")]
        for endpoint, method in endpoints:
            for body in (None, [], True, 1, "value"):
                with self.subTest(endpoint=endpoint, body=body):
                    response = self.call(endpoint, body, method)
                    self.assertEqual(response.status_code, 400, response.content[:200])
        self.file.refresh_from_db()
        self.folder.refresh_from_db()
        self.assertEqual(self.file.display_name, "Original")
        self.assertEqual(self.folder.name, "Original")
        self.assertEqual(InventoryFolder.objects.count(), 1)

    def test_non_string_fields_rejected_without_clearing_existing_data(self):
        cases = [("folders/", "post", {"name": 1}), ("folders/", "post", {"name": "New", "scope": []}),
                 ("move/", "post", {"source_id": self.file.pk, "on_duplicate": True}),
                 ("presign/", "post", {"file_id": self.file.pk, "r2_key": []}),
                 (f"files/{self.file.pk}/", "patch", {"description": False}),
                 (f"folders/{self.folder.pk}/", "patch", {"name": ["New"]})]
        for endpoint, method, body in cases:
            with self.subTest(endpoint=endpoint, body=body):
                self.assertEqual(self.call(endpoint, body, method).status_code, 400)
        self.file.refresh_from_db()
        self.assertEqual(self.file.description, "Original description")

    @patch("apps.domains.inventory.views.generate_presigned_get_url_storage", return_value="https://example.test/qa")
    @patch("apps.domains.inventory.views.do_move_file", return_value={"ok": True})
    def test_invalid_ids_never_select_another_object(self, move, presign):
        for value in (True, False, 1.9, 0, -1, "1.0", "1e0", 2**63, "9" * 5000, []):
            cases = [("folders/", {"name": "New", "parent_id": value}),
                     ("presign/", {"file_id": value, "fileId": self.file.pk}),
                     ("move/", {"source_id": value}),
                     ("move/", {"source_id": self.file.pk, "target_folder_id": value})]
            for endpoint, body in cases:
                with self.subTest(endpoint=endpoint, value=str(value)[:30]):
                    self.assertEqual(self.call(endpoint, body).status_code, 400)
        move.assert_not_called()
        presign.assert_not_called()
        self.assertEqual(InventoryFolder.objects.count(), 1)

    @patch("apps.domains.inventory.views.generate_presigned_get_url_storage", return_value="https://example.test/qa")
    def test_explicit_download_uses_owned_original_metadata_without_changing_preview(self, presign):
        response = self.call("presign/", {"file_id": self.file.pk, "download": True, "filename": "ignored.hwp"})
        self.assertEqual(response.status_code, 200)
        presign.assert_called_once_with(key=self.file.r2_key, expires_in=3600,
                                       filename=self.file.original_name, content_type=self.file.content_type or "application/octet-stream")
        presign.reset_mock()
        self.assertEqual(self.call("presign/", {"file_id": self.file.pk, "download": "true"}).status_code, 400)
        presign.assert_not_called()

    @patch("apps.domains.inventory.views.generate_presigned_get_url_storage", return_value="https://example.test/qa")
    def test_expiry_requires_positive_integer_and_keeps_one_hour_cap(self, presign):
        for expiry in (True, False, 0, -1, 1.5, "1.5", [], 2**63):
            with self.subTest(expiry=expiry):
                self.assertEqual(self.call("presign/", {"file_id": self.file.pk, "expires_in": expiry}).status_code, 400)
        presign.assert_not_called()
        for expiry, expected in ((None, 3600), ("60", 60), (7200, 3600)):
            response = self.call("presign/", {"file_id": str(self.file.pk), "expires_in": expiry})
            self.assertEqual(response.status_code, 200, response.content)
            self.assertEqual(response.json()["url"], "https://example.test/qa")
            presign.assert_called_with(key=self.file.r2_key, expires_in=expected)
        for primary in (None, ""):
            response = self.call("presign/", {"file_id": primary, "fileId": str(self.file.pk)})
            self.assertEqual(response.status_code, 200, response.content)

    def test_create_rename_and_reload_keep_numeric_string_ids_and_root(self):
        response = self.call("folders/", {"name": "Child", "parent_id": str(self.folder.pk)})
        self.assertEqual(response.status_code, 200, response.content)
        child = InventoryFolder.objects.get(pk=response.json()["id"])
        self.assertEqual(child.parent_id, self.folder.pk)
        for root in (None, ""):
            self.assertEqual(self.call("folders/", {"name": f"Root{root}", "parent_id": root}).status_code, 200)
        self.assertEqual(self.call(f"files/{self.file.pk}/", {"displayName": "Updated", "description": None}, "patch").status_code, 200)
        self.file.refresh_from_db()
        self.assertEqual(self.file.display_name, "Updated")
        self.assertEqual(self.file.description, "")

    def test_folder_name_length_matches_rename_contract(self):
        self.assertEqual(self.call("folders/", {"name": "x" * 101}).status_code, 400)
        self.assertEqual(self.call("folders/", {"name": "x" * 100}).status_code, 200)
