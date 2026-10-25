import base64
from types import SimpleNamespace
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, override_settings
from django.contrib.auth import get_user_model
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.domains.landing_public.api.serializers import PublicBoardPostWriteSerializer
from apps.domains.landing_public.api.views.board_views import PublicBoardPostViewSet
from apps.domains.landing_public.api.views.upload_views import ReviewPhotoUploadView
from apps.domains.landing_public.models import PublicBoardPost
from apps.domains.landing_public.services.photos import photo_key, photo_urls, validate_photos

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aRXsAAAAASUVORK5CYII=")
STORAGE = "apps.infrastructure.storage.r2"


@override_settings(R2_ENDPOINT="https://account.r2.cloudflarestorage.com", R2_ADMIN_BUCKET="academy-admin")
class PublicPhotoReferenceTests(SimpleTestCase):
    def setUp(self):
        self.key = f"landing-public/reviews/11/users/7/{'a' * 64}.png"
        self.url = f"https://account.r2.cloudflarestorage.com/academy-admin/{self.key}?X-Amz-Expires=1"

    def test_expired_signed_url_normalizes_to_durable_key(self):
        self.assertEqual(validate_photos([self.url, self.key], tenant_id=11, user_id=7), [self.key])
        with patch(f"{STORAGE}.generate_presigned_get_url_admin", return_value="https://fresh.test/photo") as sign:
            self.assertEqual(photo_urls([self.url], 11), ["https://fresh.test/photo"])
            sign.assert_called_once_with(key=self.key, expires_in=604800)

    def test_rejects_foreign_tenant_author_bucket_and_untrusted_urls(self):
        for value in [self.key.replace("/11/", "/12/"), self.key.replace("/7/", "/8/"),
                      self.url.replace("academy-admin", "private-bucket"), "https://other.test/photo.png",
                      "https://[malformed", self.key + "/../photo.png"]:
            with self.subTest(value=value), self.assertRaises(ValidationError):
                validate_photos([value], tenant_id=11, user_id=7)
        with patch(f"{STORAGE}.generate_presigned_get_url_admin") as sign:
            self.assertEqual(photo_urls([self.url.replace("/11/", "/12/"), "https://[malformed"], 11), [])
            sign.assert_not_called()

    def test_existing_authored_photos_and_legacy_keys_survive_edit(self):
        legacy = "landing-public/reviews/11/abc123.png"
        external = "https://legacy.example.test/author-photo.png"
        self.assertEqual(photo_key(legacy, 11), legacy)
        self.assertEqual(validate_photos([self.url, external], tenant_id=11, user_id=9, existing=[self.key, external]), [self.key, external])
        request = SimpleNamespace(tenant=SimpleNamespace(id=11), user=SimpleNamespace(id=7))
        serializer = PublicBoardPostWriteSerializer(SimpleNamespace(meta={"photos": [self.key]}), data={"meta": {"matchup_report_ids": []}}, partial=True, context={"request": request})
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(serializer.validated_data["meta"]["photos"], [self.key])

    def test_count_and_missing_identity_fail_closed(self):
        for photos, tenant, user in [([self.key] * 9, 11, 7), (self.key, 11, 7), ([self.key], None, 7), ([self.key], 11, None)]:
            with self.assertRaises(ValidationError):
                validate_photos(photos, tenant_id=tenant, user_id=user)

    def upload(self, content=PNG):
        request = SimpleNamespace(tenant=SimpleNamespace(id=11), user=SimpleNamespace(id=7), FILES={"file": SimpleUploadedFile("photo.png", content, content_type="image/png")})
        return ReviewPhotoUploadView().post(request)

    def test_response_loss_retry_reuses_author_content_key_and_complete_bytes(self):
        with patch(f"{STORAGE}.upload_fileobj_to_r2_admin") as upload, patch(f"{STORAGE}.generate_presigned_get_url_admin", side_effect=[RuntimeError("response lost"), "https://fresh.test/photo"]):
            written = []
            upload.side_effect = lambda **kw: written.append((kw["key"], kw["fileobj"].read()))
            self.assertEqual(self.upload().status_code, 500)
            response = self.upload()
            self.assertEqual(response.status_code, 201)
            self.assertEqual(written[0], written[1])
            self.assertEqual(written[0][1], PNG)
            self.assertTrue(response.data["key"].startswith("landing-public/reviews/11/users/7/"))

    def test_bad_image_or_unavailable_validator_never_writes_storage(self):
        with patch(f"{STORAGE}.upload_fileobj_to_r2_admin") as upload:
            self.assertEqual(self.upload(b"<script>not an image</script>").status_code, 400)
            with patch("apps.api.common.image_validator.is_real_image", side_effect=RuntimeError("unavailable")):
                self.assertEqual(self.upload().status_code, 503)
            upload.assert_not_called()


@override_settings(R2_ENDPOINT="https://account.r2.cloudflarestorage.com", R2_ADMIN_BUCKET="academy-admin")
class PublicPhotoPersistenceTests(TestCase):
    def test_board_author_save_reload_edit_and_tenant_boundary(self):
        tenant = Tenant.objects.create(name="Photo QA", code="qa-public-photo", is_active=True)
        user = get_user_model().objects.create_user(username="qa-public-photo-author", password="test1234", tenant=tenant)
        TenantMembership.ensure_active(tenant=tenant, user=user, role="teacher")
        factory = APIRequestFactory()
        key = f"landing-public/reviews/{tenant.id}/users/{user.id}/{'a' * 64}.png"

        def request(method, payload, pk=None):
            req = getattr(factory, method)("/landing-public/board/", payload, format="json")
            req.tenant = tenant
            force_authenticate(req, user=user)
            action = {"post": "create", "get": "retrieve", "patch": "partial_update"}[method]
            return PublicBoardPostViewSet.as_view({method: action})(req, **({"pk": pk} if pk else {}))

        with patch(f"{STORAGE}.generate_presigned_get_url_admin", side_effect=lambda **kw: f"https://account.r2.cloudflarestorage.com/academy-admin/{kw['key']}?fresh=true"):
            created = request("post", {"title": "사진 공지", "content": "<p>본문</p>", "meta": {"photos": [key]}})
            self.assertEqual(created.status_code, 201, created.data)
            obj = PublicBoardPost.objects.get(pk=created.data["id"])
            self.assertEqual(obj.meta["photos"], [key])
            fetched = request("get", {}, obj.id)
            self.assertTrue(fetched.data["is_owner_or_author"])
            self.assertIn("?fresh=true", fetched.data["meta"]["photos"][0])
            self.assertEqual(request("patch", {"content": "<p>수정</p>", "meta": {"photos": fetched.data["meta"]["photos"]}}, obj.id).status_code, 200)
            rejected = request("patch", {"meta": {"photos": [key.replace(f"/reviews/{tenant.id}/", f"/reviews/{tenant.id + 1}/")]}}, obj.id)
            self.assertEqual(rejected.status_code, 400)
            obj.refresh_from_db()
            self.assertEqual(obj.meta["photos"], [key])
            self.assertEqual(obj.content, "<p>수정</p>")
