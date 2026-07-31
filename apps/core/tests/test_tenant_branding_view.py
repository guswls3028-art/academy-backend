from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from rest_framework.test import APITestCase

from apps.core.models import Tenant, TenantMembership
from apps.core.models.program import Program


User = get_user_model()


@override_settings(OWNER_TENANT_ID=999_999)
class TenantBrandingViewTests(APITestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(
            name="Branding Academy",
            code="branding_academy",
            is_active=True,
        )
        self.other_tenant = Tenant.objects.create(
            name="Other Academy",
            code="other_branding_academy",
            is_active=True,
        )
        self.program = Program.objects.get(tenant=self.tenant)
        self.program.display_name = "Existing Brand"
        self.program.ui_config = {
            "login_title": "Welcome",
            "logo_key": f"tenant-logos/{self.tenant.id}/logo.webp",
            "theme": "preserve-me",
        }
        self.program.save(update_fields=["display_name", "ui_config"])
        self.headers = {
            "HTTP_HOST": "localhost",
            "HTTP_X_TENANT_CODE": self.tenant.code,
        }

        self.owner = User.objects.create_user(
            username=f"t{self.tenant.id}_branding_owner",
            password="test1234!",
            tenant=self.tenant,
            is_staff=True,
            is_active=True,
        )
        TenantMembership.objects.create(
            tenant=self.tenant,
            user=self.owner,
            role="owner",
            is_active=True,
        )
        self.client.force_authenticate(user=self.owner)

    @patch(
        "apps.infrastructure.storage.r2.resolve_admin_logo_url",
        return_value="https://signed.example/logo.webp",
    )
    def test_owner_reads_same_tenant_branding_with_resolved_logo(self, resolve_logo):
        response = self.client.get(
            f"/api/v1/core/tenant-branding/{self.tenant.id}/",
            **self.headers,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.data,
            {
                "tenantId": self.tenant.id,
                "loginTitle": "Welcome",
                "loginSubtitle": "",
                "logoUrl": "https://signed.example/logo.webp",
                "windowTitle": "",
                "displayName": "Existing Brand",
            },
        )
        resolve_logo.assert_called_once_with(
            logo_key=f"tenant-logos/{self.tenant.id}/logo.webp",
            logo_url=None,
        )

    def test_owner_cannot_read_another_tenant_branding(self):
        response = self.client.get(
            f"/api/v1/core/tenant-branding/{self.other_tenant.id}/",
            **self.headers,
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["detail"], "Cross-tenant access denied.")

    @patch(
        "apps.infrastructure.storage.r2.resolve_admin_logo_url",
        return_value="https://cdn.example/new-logo.png",
    )
    def test_patch_updates_contract_fields_and_preserves_unrelated_config(self, _resolve_logo):
        response = self.client.patch(
            f"/api/v1/core/tenant-branding/{self.tenant.id}/",
            {
                "loginTitle": "New title",
                "loginSubtitle": "New subtitle",
                "logoUrl": "https://cdn.example/new-logo.png",
                "windowTitle": "New window",
                "displayName": "New Brand",
            },
            format="json",
            **self.headers,
        )

        self.assertEqual(response.status_code, 200)
        self.program.refresh_from_db()
        self.assertEqual(self.program.display_name, "New Brand")
        self.assertEqual(
            self.program.ui_config,
            {
                "login_title": "New title",
                "login_subtitle": "New subtitle",
                "logo_url": "https://cdn.example/new-logo.png",
                "window_title": "New window",
                "theme": "preserve-me",
            },
        )

    @patch(
        "apps.infrastructure.storage.r2.generate_presigned_get_url_admin",
        return_value="https://signed.example/uploaded.svg",
    )
    @patch("apps.infrastructure.storage.r2.upload_fileobj_to_r2_admin")
    def test_valid_svg_upload_uses_tenant_scoped_key(self, upload, _presign):
        logo = SimpleUploadedFile(
            "brand.svg",
            b'<svg xmlns="http://www.w3.org/2000/svg"></svg>',
            content_type="image/svg+xml",
        )

        response = self.client.post(
            f"/api/v1/core/tenant-branding/{self.tenant.id}/upload-logo/",
            {"file": logo},
            format="multipart",
            **self.headers,
        )

        self.assertEqual(response.status_code, 200)
        key = f"tenant-logos/{self.tenant.id}/logo.svg"
        upload.assert_called_once()
        self.assertEqual(upload.call_args.kwargs["key"], key)
        self.assertEqual(upload.call_args.kwargs["content_type"], "image/svg+xml")
        self.program.refresh_from_db()
        self.assertEqual(self.program.ui_config["logo_key"], key)
        self.assertEqual(
            self.program.ui_config["logo_url"],
            "https://signed.example/uploaded.svg",
        )

    @patch("apps.infrastructure.storage.r2.upload_fileobj_to_r2_admin")
    def test_svg_extension_with_non_svg_content_is_rejected(self, upload):
        fake_logo = SimpleUploadedFile(
            "brand.svg",
            b"not an svg",
            content_type="image/svg+xml",
        )

        response = self.client.post(
            f"/api/v1/core/tenant-branding/{self.tenant.id}/upload-logo/",
            {"file": fake_logo},
            format="multipart",
            **self.headers,
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["detail"], "유효한 SVG 파일이 아닙니다.")
        upload.assert_not_called()
