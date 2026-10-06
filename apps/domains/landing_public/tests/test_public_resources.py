import io
import uuid
import zipfile
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from reportlab.pdfgen import canvas
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.domains.landing_public.api.views.resource_views import (
    PublicResourceFileView,
    PublicResourcePostViewSet,
    PublicResourceUploadView,
)
from apps.domains.landing_public.models.resource import (
    PublicResourceBoardAccess,
    PublicResourceFile,
    PublicResourcePost,
)
from apps.domains.landing_public.services.resource_files import (
    MAX_RESOURCE_BYTES,
    resource_content_disposition,
    validate_resource_file,
)


def pdf_bytes():
    output = io.BytesIO()
    document = canvas.Canvas(output)
    document.drawString(50, 750, "QA public resource")
    document.showPage()
    document.save()
    return output.getvalue()


def hwpx_bytes(extra=None):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as document:
        document.writestr("mimetype", "application/hwp+zip")
        document.writestr("Contents/content.hpf", "<package/>")
        document.writestr("Contents/header.xml", "<head/>")
        document.writestr("Contents/section0.xml", "<sec/>")
        if extra:
            document.writestr(*extra)
    return output.getvalue()


class PublicResourceContractTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = Tenant.objects.create(name="qa-resources", code="qa-resources", is_active=True)
        self.other = Tenant.objects.create(name="qa-other", code="qa-resource-other", is_active=True)
        self.one = self.user("qa-one", "owner")
        self.two = self.user("qa-two", "admin")
        self.staff = self.user("qa-staff", "teacher")
        self.superuser = self.user("qa-superuser", "owner", superuser=True)
        PublicResourceBoardAccess.objects.create(tenant=self.tenant, publisher_one=self.one, publisher_two=self.two)

    def user(self, name, role, superuser=False):
        user = get_user_model().objects.create_user(
            username=name,
            password="qa-only-password",
            name=name,
            tenant=self.tenant,
            is_staff=True,
            is_superuser=superuser,
        )
        TenantMembership.ensure_active(tenant=self.tenant, user=user, role=role)
        return user

    def call(self, method, action=None, body=None, user=None, tenant=None, pk=None, file_id=None):
        request = getattr(self.factory, method)(
            "/qa/", data=body or {}, format="multipart" if action == "upload" else "json"
        )
        request.tenant = tenant or self.tenant
        if user is not None:
            force_authenticate(request, user=user)
        if action == "upload":
            return PublicResourceUploadView.as_view()(request)
        if file_id:
            return PublicResourceFileView.as_view()(request, file_id=file_id)
        return PublicResourcePostViewSet.as_view({method: action})(request, **({"pk": pk} if pk else {}))

    def file(self, user=None, tenant=None, post=None):
        file_id = uuid.uuid4()
        return PublicResourceFile.objects.create(
            id=file_id,
            tenant=tenant or self.tenant,
            uploaded_by=user or self.one,
            post=post,
            storage_key=f"qa/{file_id}.pdf",
            filename="qa-report.pdf",
            size=100,
            extension="pdf",
            is_ready=True,
            content_type="application/pdf",
        )

    def body(self, file, **changes):
        return {
            "request_id": str(uuid.uuid4()),
            "title": "qa report",
            "category": "matchup",
            "content": "qa content",
            "file_ids": [str(file.id)],
            **changes,
        }

    def publish(self, user=None, category="matchup"):
        file = self.file(user=user)
        response = self.call("post", "create", self.body(file, category=category), user=user or self.one)
        self.assertEqual(response.status_code, 201, response.data)
        return PublicResourcePost.objects.get(pk=response.data["id"]), file

    def test_both_publishers_and_anonymous_reload_in_both_categories(self):
        for user, category in ((self.one, "matchup"), (self.two, "analysis")):
            post, file = self.publish(user, category)
            response = self.call("get", "retrieve", pk=post.pk)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.data["files"][0]["id"], str(file.pk))
            self.assertNotIn("storage_key", response.data["files"][0])
            self.assertNotIn("author", response.data)
        self.assertEqual(PublicResourcePost.objects.count(), 2)

    def test_other_staff_superuser_and_anonymous_cannot_write(self):
        for user in (None, self.staff, self.superuser):
            response = self.call("post", "create", self.body(self.file()), user=user)
            self.assertIn(response.status_code, (401, 403))
        self.assertFalse(PublicResourcePost.objects.exists())

    def test_missing_config_revocation_and_student_role_deny(self):
        file = self.file()
        TenantMembership.objects.filter(user=self.one, tenant=self.tenant).update(is_active=False)
        self.assertEqual(self.call("post", "create", self.body(file), user=self.one).status_code, 403)
        TenantMembership.objects.filter(user=self.one, tenant=self.tenant).update(is_active=True, role="student")
        self.assertEqual(self.call("post", "create", self.body(file), user=self.one).status_code, 403)
        PublicResourceBoardAccess.objects.all().delete()
        self.assertEqual(self.call("post", "create", self.body(file), user=self.two).status_code, 403)

    def test_inactive_user_and_cross_tenant_configuration_deny(self):
        self.one.is_active = False
        self.one.save(update_fields=["is_active"])
        self.assertEqual(self.call("post", "create", self.body(self.file()), user=self.one).status_code, 403)
        self.assertEqual(
            self.call("post", "create", self.body(self.file()), user=self.two, tenant=self.other).status_code, 403
        )

    def test_other_uploader_pending_and_cross_tenant_files_deny(self):
        for file in (self.file(user=self.two), self.file(tenant=self.other)):
            response = self.call("post", "create", self.body(file), user=self.one)
            self.assertEqual(response.status_code, 400)
            file.refresh_from_db()
            self.assertIsNone(file.post_id)
        self.assertFalse(PublicResourcePost.objects.exists())

    def test_either_publisher_retains_existing_file_and_cannot_move_it(self):
        post, file = self.publish()
        new_file = self.file(user=self.two)
        response = self.call(
            "patch",
            "partial_update",
            self.body(file, title="updated", file_ids=[str(file.id), str(new_file.id)]),
            user=self.two,
            pk=post.pk,
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(len(response.data["files"]), 2)
        self.assertEqual(self.call("post", "create", self.body(file), user=self.one).status_code, 400)
        self.assertEqual(PublicResourcePost.objects.count(), 1)

    def test_duplicate_create_retry_reuses_post_and_conflict_does_not_mutate(self):
        file = self.file()
        body = self.body(file)
        first = self.call("post", "create", body, user=self.one)
        second = self.call("post", "create", body, user=self.one)
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.data["id"], second.data["id"])
        response = self.call("post", "create", {**body, "title": "changed"}, user=self.one)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(PublicResourcePost.objects.count(), 1)
        self.assertEqual(PublicResourcePost.objects.first().title, body["title"])

    @patch(
        "apps.domains.landing_public.api.views.resource_views.generate_presigned_get_url_admin",
        return_value="https://qa.invalid/signed",
    )
    def test_anonymous_link_checks_pending_deleted_removed_and_tenant(self, sign):
        pending = self.file()
        self.assertEqual(self.call("get", file_id=pending.pk).status_code, 404)
        post, file = self.publish()
        response = self.call("get", file_id=file.pk)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Cache-Control"], "no-store")
        sign.assert_called_once_with(key=file.storage_key, expires_in=300)
        self.assertEqual(self.call("get", file_id=file.pk, tenant=self.other).status_code, 404)
        file.is_removed = True
        file.save(update_fields=["is_removed"])
        self.assertEqual(self.call("get", file_id=file.pk).status_code, 404)
        file.is_removed = False
        file.save(update_fields=["is_removed"])
        self.assertEqual(self.call("delete", "destroy", user=self.two, pk=post.pk).status_code, 204)
        self.assertEqual(self.call("get", "retrieve", pk=post.pk).status_code, 404)
        self.assertEqual(self.call("get", file_id=file.pk).status_code, 404)
        self.assertEqual(PublicResourceFile.objects.count(), 2)

    @patch("apps.domains.landing_public.api.views.resource_views.delete_object_r2_admin")
    def test_pending_cleanup_is_uploader_only_and_preserves_attached_manual_file(self, delete):
        pending = self.file()
        self.assertEqual(self.call("delete", user=self.two, file_id=pending.pk).status_code, 404)
        self.assertEqual(self.call("delete", user=self.one, file_id=pending.pk).status_code, 204)
        post, file = self.publish()
        self.assertEqual(self.call("delete", user=self.one, file_id=file.pk).status_code, 404)
        delete.assert_called_once_with(key=pending.storage_key)
        self.assertTrue(PublicResourceFile.objects.filter(pk=file.pk).exists())

    @patch("apps.domains.landing_public.api.views.resource_views.delete_object_r2_admin")
    @patch("apps.domains.landing_public.api.views.resource_views.upload_fileobj_to_r2_admin")
    def test_upload_success_and_failure_cleanup(self, upload, delete):
        response = self.call(
            "post",
            "upload",
            {"file": SimpleUploadedFile("한글.PDF", pdf_bytes(), content_type="application/octet-stream")},
            user=self.two,
        )
        self.assertEqual(response.status_code, 201, response.data)
        file = PublicResourceFile.objects.get(pk=response.data["id"])
        self.assertIsNone(file.post_id)
        self.assertIn(f"/resources/{self.tenant.pk}/", file.storage_key)
        self.assertIn("filename*=UTF-8", upload.call_args.kwargs["content_disposition"])
        upload.side_effect = OSError("qa-storage-failed")
        response = self.call("post", "upload", {"file": SimpleUploadedFile("fail.pdf", pdf_bytes())}, user=self.one)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(PublicResourceFile.objects.count(), 1)
        self.assertEqual(delete.call_count, 1)

    @patch("apps.domains.landing_public.api.views.resource_views.delete_object_r2_admin")
    @patch("apps.domains.landing_public.api.views.resource_views.upload_fileobj_to_r2_admin")
    def test_upload_revocation_during_storage_write_cleans_pending_file(self, upload, delete):
        def revoke(**kwargs):
            get_user_model().objects.filter(pk=self.one.pk).update(is_active=False)

        upload.side_effect = revoke
        response = self.call("post", "upload", {"file": SimpleUploadedFile("revoked.pdf", pdf_bytes())}, user=self.one)
        self.assertEqual(response.status_code, 503)
        self.assertFalse(PublicResourceFile.objects.exists())
        delete.assert_called_once()

    def test_invalid_category_empty_attachments_and_duplicate_ids_do_not_publish(self):
        file = self.file()
        for changes in ({"category": "other"}, {"file_ids": []}, {"file_ids": [str(file.id), str(file.id)]}):
            self.assertEqual(self.call("post", "create", self.body(file, **changes), user=self.one).status_code, 400)
        self.assertFalse(PublicResourcePost.objects.exists())

    @patch("apps.domains.landing_public.api.views.resource_views.delete_object_r2_admin")
    def test_failed_pending_cleanup_is_not_attachable_and_retry_cleans(self, delete):
        file = self.file()
        delete.side_effect = OSError("qa storage cleanup failure")
        self.assertEqual(self.call("delete", user=self.one, file_id=file.pk).status_code, 503)
        file.refresh_from_db()
        self.assertFalse(file.is_ready)
        self.assertEqual(self.call("post", "create", self.body(file), user=self.one).status_code, 400)
        delete.side_effect = None
        self.assertEqual(self.call("delete", user=self.one, file_id=file.pk).status_code, 204)
        self.assertFalse(PublicResourceFile.objects.filter(pk=file.pk).exists())

    @patch("apps.domains.landing_public.api.views.resource_views.delete_object_r2_admin")
    def test_database_cleanup_failure_cannot_publish_missing_object(self, delete):
        from django.db import DatabaseError

        file = self.file()
        with patch("django.db.models.query.QuerySet.delete", side_effect=DatabaseError("qa database cleanup failure")):
            self.assertEqual(self.call("delete", user=self.one, file_id=file.pk).status_code, 503)
        file.refresh_from_db()
        self.assertFalse(file.is_ready)
        self.assertEqual(self.call("post", "create", self.body(file), user=self.one).status_code, 400)
        self.assertEqual(self.call("delete", user=self.one, file_id=file.pk).status_code, 204)

    @patch("apps.domains.landing_public.api.views.resource_views.upload_fileobj_to_r2_admin")
    def test_generic_upload_publish_reload_and_anonymous_download(self, upload):
        response = self.call("post", "upload", {
            "file": SimpleUploadedFile('분석.긴확장자입니다', b"original bytes", content_type="text/html"),
        }, user=self.one)
        self.assertEqual(response.status_code, 201, response.data)
        file = PublicResourceFile.objects.get(pk=response.data["id"])
        self.assertEqual(file.content_type, "application/octet-stream")
        self.assertEqual(response.data["extension"], "긴확장자입니다")
        self.assertEqual(file.extension, "")
        self.assertTrue(file.storage_key.endswith(str(file.id)))
        self.assertEqual(upload.call_args.kwargs["content_type"], "application/octet-stream")
        created = self.call("post", "create", self.body(file), user=self.one)
        self.assertEqual(created.status_code, 201, created.data)
        reread = self.call("get", "retrieve", pk=created.data["id"])
        self.assertEqual(reread.data["files"][0]["filename"], '분석.긴확장자입니다')
        with patch("apps.domains.landing_public.api.views.resource_views.generate_presigned_get_url_admin", return_value="https://qa.invalid/signed"):
            self.assertEqual(self.call("get", file_id=file.pk).status_code, 200)
            self.assertEqual(self.call("get", file_id=file.pk, tenant=self.other).status_code, 404)

    def test_stale_edit_preserves_newer_content_and_removed_file_while_retry_succeeds(self):
        post, file = self.publish()
        version = post.updated_at.isoformat()
        replacement = self.file(user=self.two)
        update = self.body(replacement, title="newer", expected_updated_at=version)
        first = self.call("patch", "partial_update", update, user=self.two, pk=post.pk)
        self.assertEqual(first.status_code, 200, first.data)
        replay = self.call("patch", "partial_update", update, user=self.two, pk=post.pk)
        self.assertEqual(replay.status_code, 200, replay.data)
        self.assertEqual(replay.data["updated_at"], first.data["updated_at"])
        stale = self.call("patch", "partial_update", self.body(file, title="stale", expected_updated_at=version), user=self.one, pk=post.pk)
        self.assertEqual(stale.status_code, 409, stale.data)
        post.refresh_from_db(); file.refresh_from_db()
        self.assertEqual(post.title, "newer")
        self.assertTrue(file.is_removed)
        recovered = self.call("patch", "partial_update", self.body(replacement, title="reviewed", expected_updated_at=first.data["updated_at"]), user=self.one, pk=post.pk)
        self.assertEqual(recovered.status_code, 200, recovered.data)

    def test_publisher_ids_are_distinct_at_database_boundary(self):
        from django.db import IntegrityError, transaction

        access = PublicResourceBoardAccess.objects.get(tenant=self.tenant)
        with self.assertRaises(IntegrityError), transaction.atomic():
            access.publisher_two = self.one
            access.save(update_fields=["publisher_two"])
        access.refresh_from_db()
        self.assertEqual(access.publisher_two_id, self.two.pk)


class ResourceFormatTests(TestCase):
    def test_arbitrary_original_formats_and_safe_download_names(self):
        for name in ("분석.xlsx", "발표.pptx", "자료.zip", "자료.긴확장자입니다", "README", "a.html", 'quoted.x"y'):
            with self.subTest(name=name):
                upload = SimpleUploadedFile(name, b"original bytes", content_type="text/html")
                filename, extension, mime = validate_resource_file(upload)
                self.assertEqual(filename, name)
                self.assertEqual(extension, name.rsplit(".", 1)[-1].lower() if "." in name else "")
                self.assertEqual(mime, "application/octet-stream")
                self.assertEqual(upload.read(), b"original bytes")
                header = resource_content_disposition(name)
                header.encode("ascii")
                self.assertTrue(header.startswith('attachment; filename="document'))
                self.assertEqual(header.count('"'), 2)


    def test_pdf_and_hwpx_case_insensitive_with_generic_mime(self):
        for name, data, extension in [("보고서.PdF", pdf_bytes(), "pdf"), ("분석.HWPX", hwpx_bytes(), "hwpx")]:
            file = SimpleUploadedFile(name, data, content_type="application/octet-stream")
            self.assertEqual(validate_resource_file(file)[1], extension)
            self.assertEqual(file.tell(), 0)

    def test_invalid_and_mismatched_formats(self):
        for name, data in [
            ("empty.pdf", b""),
            ("fake.pdf", b"%PDF-1.4 invalid %%EOF"),
            ("wrong.hwp", hwpx_bytes()),
            ("wrong.hwpx", pdf_bytes()),
            ("bad.hwpx", b"PKbroken"),
        ]:
            with self.subTest(name=name), self.assertRaises(ValidationError):
                validate_resource_file(SimpleUploadedFile(name, data))

    def test_hwpx_traversal_and_expansion_denied(self):
        for extra in [("../private.xml", "<evil/>"), ("bomb.xml", b"x" * (2 * 1024 * 1024))]:
            with self.assertRaises(ValidationError):
                validate_resource_file(SimpleUploadedFile("unsafe.hwpx", hwpx_bytes(extra)))

    def test_limit_controls_and_disposition(self):
        file = SimpleUploadedFile("large.pdf", pdf_bytes())
        file.size = MAX_RESOURCE_BYTES + 1
        with self.assertRaises(ValidationError):
            validate_resource_file(file)
        with self.assertRaises(ValidationError):
            validate_resource_file(SimpleUploadedFile("bad\rname.pdf", pdf_bytes()))
        disposition = resource_content_disposition('한글 "보고서".pdf')
        self.assertNotIn("\r", disposition)
        self.assertIn("%22", disposition)
        self.assertIn("filename*=UTF-8", disposition)
