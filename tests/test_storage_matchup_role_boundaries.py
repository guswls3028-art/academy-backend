"""Real JWT requests must use the current tenant role, never Django staff flags."""

from django.contrib.auth import get_user_model
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase, override_settings
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.test import APIClient, APIRequestFactory
from rest_framework_simplejwt.tokens import AccessToken

from apps.core.models import Tenant, TenantMembership
from apps.domains.inventory.models import InventoryFile
from apps.domains.inventory.views import InventoryListView
from apps.domains.matchup.models import MatchupDocument, MatchupHitReport
from apps.domains.parents.models import Parent
from apps.domains.students.models import Student


@override_settings(
    ALLOWED_HOSTS=["api.hakwonplus.com", "testserver"],
    TENANT_HEADER_CODE_ALLOWED_HOSTS=("api.hakwonplus.com",),
)
class StorageMatchupRoleBoundaryTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(code="qa-storage-roles", name="Role QA")
        cls.other_tenant = Tenant.objects.create(code="qa-storage-other", name="Other QA")
        cls.users = {}
        for role in ("owner", "admin", "staff", "teacher", "student", "parent"):
            user = get_user_model().objects.create_user(
                username=f"qa-storage-{role}", tenant=cls.tenant, is_staff=True,
                must_change_password=False,
            )
            TenantMembership.ensure_active(tenant=cls.tenant, user=user, role=role)
            cls.users[role] = user
        cls.parent = Parent.objects.create(
            tenant=cls.tenant, user=cls.users["parent"], name="QA Parent", phone="01000001111",
        )
        cls.student = Student.objects.create(
            tenant=cls.tenant, user=cls.users["student"], parent=cls.parent,
            ps_number="ROLE01", omr_code="10000001", name="QA Student",
        )
        cls.file = InventoryFile.objects.create(
            tenant=cls.tenant, scope="admin", display_name="Original", original_name="qa.pdf",
            r2_key=f"tenants/{cls.tenant.id}/qa-role.pdf", content_type="application/pdf",
        )
        cls.student_file = InventoryFile.objects.create(
            tenant=cls.tenant, scope="student", student_ps=cls.student.ps_number,
            display_name="Student original", original_name="qa.pdf",
            r2_key=f"tenants/{cls.tenant.id}/qa-student-role.pdf",
        )
        cls.document = MatchupDocument.objects.create(
            tenant=cls.tenant, inventory_file=cls.file, author=cls.users["teacher"],
            title="QA document", status="done",
        )
        cls.own_report = MatchupHitReport.objects.create(
            tenant=cls.tenant, document=cls.document, author=cls.users["teacher"], title="Own",
        )
        cls.other_report = MatchupHitReport.objects.create(
            tenant=cls.tenant, document=cls.document, author=cls.users["owner"], title="Other",
        )

    def client_for(self, role, *, tenant=None):
        user = self.users[role]
        token = AccessToken.for_user(user)
        token["tenant_id"] = (tenant or self.tenant).id
        token["token_version"] = user.token_version or 0
        client = APIClient(
            HTTP_HOST="api.hakwonplus.com", HTTP_X_TENANT_CODE=(tenant or self.tenant).code,
        )
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        return client

    def test_student_and_parent_global_staff_flag_cannot_read_or_edit_admin_storage(self):
        for role in ("student", "parent"):
            with self.subTest(role=role):
                client = self.client_for(role)
                listing = client.get("/api/v1/storage/inventory/?scope=admin")
                self.assertEqual(listing.status_code, 403, listing.content)
                edited = client.patch(
                    f"/api/v1/storage/inventory/files/{self.file.id}/?scope=admin",
                    {"displayName": "Unauthorized"}, format="json",
                )
                self.assertEqual(edited.status_code, 403, edited.content)
                self.file.refresh_from_db()
                self.assertEqual(self.file.display_name, "Original")

    def test_student_and_parent_keep_selected_child_storage_edit_and_reload(self):
        for role in ("student", "parent"):
            with self.subTest(role=role):
                client = self.client_for(role)
                headers = {"HTTP_X_STUDENT_ID": str(self.student.id)} if role == "parent" else {}
                suffix = f"?scope=student&student_ps={self.student.ps_number}"
                response = client.patch(
                    f"/api/v1/storage/inventory/files/{self.student_file.id}/{suffix}",
                    {"displayName": f"Saved by {role}"}, format="json", **headers,
                )
                self.assertEqual(response.status_code, 200, response.content)
                self.student_file.refresh_from_db()
                self.assertEqual(self.student_file.display_name, f"Saved by {role}")
                listing = client.get(f"/api/v1/storage/inventory/{suffix}", **headers)
                self.assertEqual(listing.status_code, 200, listing.content)
                self.assertIn(f"Saved by {role}", listing.content.decode())

    def test_staff_roles_keep_admin_storage_update_and_reload_without_global_flags(self):
        for role in ("owner", "admin", "staff", "teacher"):
            with self.subTest(role=role):
                self.users[role].is_staff = False
                self.users[role].save(update_fields=["is_staff"])
                client = self.client_for(role)
                response = client.patch(
                    f"/api/v1/storage/inventory/files/{self.file.id}/?scope=admin",
                    {"displayName": f"Saved by {role}"}, format="json",
                )
                self.assertEqual(response.status_code, 200, response.content)
                self.file.refresh_from_db()
                self.assertEqual(self.file.display_name, f"Saved by {role}")
                self.assertContains(client.get("/api/v1/storage/inventory/?scope=admin"), f"Saved by {role}")

    def test_limited_roles_cannot_enter_matchup_even_with_global_staff_flags(self):
        for role in ("student", "parent", "staff"):
            with self.subTest(role=role):
                client = self.client_for(role)
                self.assertEqual(client.get("/api/v1/matchup/hit-reports/").status_code, 403)
                response = client.patch(
                    f"/api/v1/matchup/hit-reports/{self.other_report.id}/",
                    {"title": "Unauthorized"}, format="json",
                )
                self.assertEqual(response.status_code, 403, response.content)
                self.other_report.refresh_from_db()
                self.assertEqual(self.other_report.title, "Other")

    def test_teacher_cannot_read_or_edit_another_authors_report(self):
        for role in ("teacher",):
            with self.subTest(role=role):
                client = self.client_for(role)
                response = client.get("/api/v1/matchup/hit-reports/")
                self.assertEqual(response.status_code, 200, response.content)
                ids = {item["id"] for item in response.json()["reports"]}
                self.assertNotIn(self.other_report.id, ids)
                edit = client.patch(
                    f"/api/v1/matchup/hit-reports/{self.other_report.id}/",
                    {"title": "Unauthorized"}, format="json",
                )
                self.assertEqual(edit.status_code, 403, edit.content)
                self.other_report.refresh_from_db()
                self.assertEqual(self.other_report.title, "Other")

    def test_author_can_edit_draft_but_cannot_bypass_submitted_lock(self):
        client = self.client_for("teacher")
        path = f"/api/v1/matchup/hit-reports/{self.own_report.id}/"
        response = client.patch(path, {"title": "Saved draft"}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.own_report.refresh_from_db()
        self.assertEqual(self.own_report.title, "Saved draft")
        self.own_report.status = "submitted"
        self.own_report.save(update_fields=["status"])
        blocked = client.patch(path, {"title": "Bypassed"}, format="json")
        self.assertEqual(blocked.status_code, 403, blocked.content)
        self.assertEqual(blocked.json()["code"], "submitted_locked")
        self.own_report.refresh_from_db()
        self.assertEqual(self.own_report.title, "Saved draft")

    def test_owner_admin_can_review_and_edit_submitted_other_author_report(self):
        self.own_report.status = "submitted"
        self.own_report.save(update_fields=["status"])
        for role in ("owner", "admin"):
            with self.subTest(role=role):
                client = self.client_for(role)
                response = client.patch(
                    f"/api/v1/matchup/hit-reports/{self.own_report.id}/",
                    {"title": f"Reviewed by {role}"}, format="json",
                )
                self.assertEqual(response.status_code, 200, response.content)
                self.own_report.refresh_from_db()
                self.assertEqual(self.own_report.title, f"Reviewed by {role}")
                listing = client.get("/api/v1/matchup/hit-reports/")
                self.assertContains(listing, f"Reviewed by {role}")

    def test_global_superuser_flag_does_not_override_current_teacher_role(self):
        user = self.users["teacher"]
        user.is_superuser = True
        user.save(update_fields=["is_superuser"])
        response = self.client_for("teacher").patch(
            f"/api/v1/matchup/hit-reports/{self.other_report.id}/",
            {"title": "Unauthorized"}, format="json",
        )
        self.assertEqual(response.status_code, 403, response.content)
        self.other_report.refresh_from_db()
        self.assertEqual(self.other_report.title, "Other")

    def test_only_owner_and_admin_can_enter_segmentation_review(self):
        for role in self.users:
            with self.subTest(role=role):
                response = self.client_for(role).get("/api/v1/matchup/proposals/")
                self.assertEqual(response.status_code, 200 if role in ("owner", "admin") else 403)

    def test_active_teacher_role_works_without_global_staff_flag(self):
        user = self.users["teacher"]
        user.is_staff = False
        user.save(update_fields=["is_staff"])
        response = self.client_for("teacher").get("/api/v1/matchup/hit-reports/")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual([item["id"] for item in response.json()["reports"]], [self.own_report.id])

    def test_foreign_tenant_admin_cannot_read_or_change_original_objects(self):
        user = self.users["admin"]
        TenantMembership.ensure_active(tenant=self.other_tenant, user=user, role="admin")
        client = self.client_for("admin", tenant=self.other_tenant)
        listing = client.get("/api/v1/storage/inventory/?scope=admin")
        self.assertEqual(listing.status_code, 200, listing.content)
        self.assertEqual(listing.json()["files"], [])
        for path, body in (
            (f"/api/v1/storage/inventory/files/{self.file.id}/?scope=admin", {"displayName": "Cross tenant"}),
            (f"/api/v1/matchup/hit-reports/{self.other_report.id}/", {"title": "Cross tenant"}),
        ):
            with self.subTest(path=path):
                response = client.patch(path, body, format="json")
                self.assertEqual(response.status_code, 404, response.content)
        self.file.refresh_from_db()
        self.other_report.refresh_from_db()
        self.assertEqual(self.file.display_name, "Original")
        self.assertEqual(self.other_report.title, "Other")

    def test_revoked_membership_returns_401_without_mutation(self):
        client = self.client_for("teacher")
        client.raise_request_exception = False
        TenantMembership.objects.filter(tenant=self.tenant, user=self.users["teacher"]).update(is_active=False)
        for path, body in (
            (f"/api/v1/storage/inventory/files/{self.file.id}/?scope=admin", {"displayName": "Revoked"}),
            (f"/api/v1/matchup/hit-reports/{self.own_report.id}/", {"title": "Revoked"}),
        ):
            with self.subTest(path=path):
                response = client.patch(path, body, format="json")
                self.assertEqual(response.status_code, 401, response.content)
                self.assertIn("Bearer", response.headers["WWW-Authenticate"])
        self.file.refresh_from_db()
        self.own_report.refresh_from_db()
        self.assertEqual(self.file.display_name, "Original")
        self.assertEqual(self.own_report.title, "Own")


class InventoryAuthenticationResponseTests(SimpleTestCase):
    def test_known_authentication_failure_is_401_instead_of_server_error(self):
        request = APIRequestFactory().get("/api/v1/storage/inventory/")
        request.tenant = object()
        with patch(
            "apps.domains.inventory.views.JWTAuthentication.authenticate",
            side_effect=AuthenticationFailed("Expired session"),
        ):
            response = InventoryListView.as_view()(request)
        self.assertEqual(response.status_code, 401)
        self.assertIn("Bearer", response.headers["WWW-Authenticate"])

    def test_unexpected_service_failure_is_not_misreported_as_login_failure(self):
        request = APIRequestFactory().get("/api/v1/storage/inventory/")
        request.tenant = object()
        with patch(
            "apps.domains.inventory.views.JWTAuthentication.authenticate",
            side_effect=RuntimeError("Synthetic authentication service failure"),
        ), self.assertRaises(RuntimeError):
            InventoryListView.as_view()(request)
