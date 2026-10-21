import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import IntegrityError, close_old_connections, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase
from django.urls import resolve, reverse
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.domains.messaging.models import MessageTemplate, MessageTemplateCreateRequest


class TemplateCreateFixture:
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = Tenant.objects.create(code="template-create-request", name="Template create request")
        self.owner = get_user_model().objects.create_user(
            username="template-create-request-owner", tenant=self.tenant, is_staff=True,
        )
        TenantMembership.ensure_active(tenant=self.tenant, user=self.owner, role="owner")
        self.path = reverse("messaging-templates")
        self.payload = {"name": "One save intent", "body": "Saved body", "category": "grades",
                        "client_request_id": str(uuid.uuid4())}

    def call(self, method, data=None, *, tenant=None, user=None, path=None, anonymous=False):
        path = path or self.path
        request = getattr(self.factory, method)(path, data=data or {}, format="json")
        request.tenant = tenant or self.tenant
        if not anonymous:
            force_authenticate(request, user=user or self.owner)
        match = resolve(path)
        return match.func(request, **match.kwargs)

class MessageTemplateCreateRequestTests(TemplateCreateFixture, TestCase):
    def test_response_loss_retry_returns_original_id_without_creating_a_second_template(self):
        first = self.call("post", self.payload)
        self.assertEqual(first.status_code, 201)
        retry = self.call("post", self.payload)
        self.assertEqual(retry.status_code, 200)
        self.assertEqual(retry.data["id"], first.data["id"])
        self.assertEqual(retry.data["updated_at"], first.data["updated_at"])
        self.assertEqual(MessageTemplate.objects.filter(tenant=self.tenant).count(), 1)

    def test_same_key_different_payload_conflicts_without_mutating_saved_template(self):
        first = self.call("post", self.payload)
        self.assertEqual(first.status_code, 201)
        for field, value in {"name": "Different name", "subject": "Different subject", "body": "Different body",
                             "category": "attendance", "is_user_default": True}.items():
            with self.subTest(field=field):
                response = self.call("post", {**self.payload, field: value})
                self.assertEqual(response.status_code, 409)
                self.assertEqual(response.data["code"], "template_request_conflict")
                self.assertNotIn("current_template", response.data)
        self.assertEqual(MessageTemplate.objects.filter(tenant=self.tenant).count(), 1)
        current = MessageTemplate.objects.get(pk=first.data["id"])
        self.assertEqual(current.body, self.payload["body"])
        self.assertFalse(current.is_user_default)

    def test_creation_retry_returns_later_edits_without_reapplying_original_default(self):
        first = self.call("post", {**self.payload, "is_user_default": True})
        self.assertEqual(first.status_code, 201)
        detail_path = reverse("messaging-template-detail", kwargs={"pk": first.data["id"]})
        edited = self.call("patch", {"body": "Later author revision", "expected_updated_at": first.data["updated_at"]}, path=detail_path)
        self.assertEqual(edited.status_code, 200)
        other = self.call("post", {"name": "New default", "body": "New default body", "category": "grades", "is_user_default": True})
        self.assertEqual(other.status_code, 201)
        retry = self.call("post", {**self.payload, "is_user_default": True})
        self.assertEqual(retry.status_code, 200)
        self.assertEqual(retry.data["id"], first.data["id"])
        self.assertEqual(retry.data["body"], "Later author revision")
        self.assertEqual(retry.data["updated_at"], edited.data["updated_at"])
        self.assertFalse(retry.data["is_user_default"])
        self.assertTrue(MessageTemplate.objects.get(pk=other.data["id"]).is_user_default)
        self.assertNotIn("client_request_id", retry.data)

    def test_deleted_template_is_not_recreated_by_a_late_retry(self):
        first = self.call("post", self.payload)
        detail_path = reverse("messaging-template-detail", kwargs={"pk": first.data["id"]})
        self.assertEqual(self.call("delete", path=detail_path).status_code, 204)
        receipt = MessageTemplateCreateRequest.objects.get(tenant=self.tenant)
        self.assertIsNone(receipt.template_id)
        retry = self.call("post", self.payload)
        self.assertEqual(retry.status_code, 409)
        self.assertEqual(retry.data["code"], "template_create_deleted")
        self.assertFalse(MessageTemplate.objects.filter(tenant=self.tenant).exists())

    def test_different_keys_do_not_deduplicate_identical_user_authored_templates(self):
        first = self.call("post", self.payload)
        second = self.call("post", {**self.payload, "client_request_id": str(uuid.uuid4())})
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 201)
        self.assertNotEqual(first.data["id"], second.data["id"])

    def test_legacy_keyless_creation_and_existing_rows_remain_supported(self):
        legacy = {key: value for key, value in self.payload.items() if key != "client_request_id"}
        first = self.call("post", legacy)
        second = self.call("post", legacy)
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 201)
        self.assertNotEqual(first.data["id"], second.data["id"])
        self.assertFalse(MessageTemplateCreateRequest.objects.filter(tenant=self.tenant).exists())
        detail_path = reverse("messaging-template-detail", kwargs={"pk": first.data["id"]})
        self.assertEqual(self.call("patch", {"body": "Legacy row edit"}, path=detail_path).status_code, 200)

    def test_invalid_uuid_or_reuse_on_patch_is_rejected(self):
        for value in [None, "", "not-a-uuid", True, 12, {"invalid": True}]:
            with self.subTest(value=value):
                self.assertEqual(self.call("post", {**self.payload, "client_request_id": value}).status_code, 400)
        self.assertFalse(MessageTemplate.objects.filter(tenant=self.tenant).exists())
        first = self.call("post", self.payload)
        detail_path = reverse("messaging-template-detail", kwargs={"pk": first.data["id"]})
        self.assertEqual(self.call("patch", {"client_request_id": self.payload["client_request_id"]}, path=detail_path).status_code, 400)

    def test_uuid_is_tenant_scoped_and_does_not_return_foreign_template(self):
        first = self.call("post", self.payload)
        tenant = Tenant.objects.create(code="template-create-other", name="Other")
        owner = get_user_model().objects.create_user(username="template-create-other-owner", tenant=tenant, is_staff=True)
        TenantMembership.ensure_active(tenant=tenant, user=owner, role="owner")
        other = self.call("post", self.payload, tenant=tenant, user=owner)
        self.assertEqual(other.status_code, 201)
        self.assertNotEqual(other.data["id"], first.data["id"])
        foreign_attempt = self.call("post", self.payload, tenant=self.tenant, user=owner)
        self.assertEqual(foreign_attempt.status_code, 403)
        self.assertNotIn("id", foreign_attempt.data)
        self.assertEqual(MessageTemplateCreateRequest.objects.count(), 2)

    def test_inconsistent_cross_tenant_receipt_fails_closed(self):
        self.assertEqual(self.call("post", self.payload).status_code, 201)
        foreign = Tenant.objects.create(code="template-create-corrupt", name="Foreign")
        template = MessageTemplate.objects.create(tenant=foreign, name="Foreign private", body="Foreign private body")
        MessageTemplateCreateRequest.objects.filter(tenant=self.tenant).update(template=template)
        retry = self.call("post", self.payload)
        self.assertEqual(retry.status_code, 409)
        self.assertEqual(retry.data["code"], "template_request_conflict")
        self.assertNotIn("id", retry.data)
        self.assertNotIn("Foreign private body", str(retry.data))
        template.refresh_from_db()
        self.assertEqual(template.body, "Foreign private body")

    def test_nonstaff_and_anonymous_cannot_create_or_replay_receipts(self):
        first = self.call("post", self.payload)
        self.assertEqual(first.status_code, 201)
        response = self.call("post", self.payload, anonymous=True)
        self.assertIn(response.status_code, [401, 403])
        for role in ["student", "parent"]:
            with self.subTest(role=role):
                user = get_user_model().objects.create_user(username=f"template-create-{role}", tenant=self.tenant)
                TenantMembership.ensure_active(tenant=self.tenant, user=user, role=role)
                response = self.call("post", self.payload, user=user)
                self.assertEqual(response.status_code, 403)
                self.assertNotIn("id", response.data)
        self.assertEqual(MessageTemplateCreateRequest.objects.count(), 1)

    def test_receipt_failure_rolls_back_creation_and_previous_default(self):
        previous = MessageTemplate.objects.create(tenant=self.tenant, name="Previous default", body="Previous", category="grades", is_user_default=True)
        with patch.object(MessageTemplateCreateRequest.objects, "create", side_effect=IntegrityError("Synthetic receipt failure")):
            with self.assertRaises(IntegrityError):
                self.call("post", {**self.payload, "is_user_default": True})
        previous.refresh_from_db()
        self.assertTrue(previous.is_user_default)
        self.assertEqual(MessageTemplate.objects.filter(tenant=self.tenant).count(), 1)
        self.assertFalse(MessageTemplateCreateRequest.objects.filter(tenant=self.tenant).exists())

    def test_database_rejects_duplicate_receipt_and_tenant_delete_cleans_receipts(self):
        created = self.call("post", self.payload)
        self.assertEqual(created.status_code, 201)
        receipt = MessageTemplateCreateRequest.objects.get(tenant=self.tenant)
        with self.assertRaises(IntegrityError), transaction.atomic():
            MessageTemplateCreateRequest.objects.create(tenant=self.tenant, client_request_id=receipt.client_request_id,
                payload_fingerprint=receipt.payload_fingerprint, template=receipt.template)
        tenant_id = self.tenant.pk
        self.tenant.delete()
        self.assertFalse(MessageTemplateCreateRequest.objects.filter(tenant_id=tenant_id).exists())


@skipUnless(connection.vendor == "postgresql", "PostgreSQL row-lock concurrency contract")
class MessageTemplateCreateRequestPostgreSQLTests(TemplateCreateFixture, TransactionTestCase):
    def test_simultaneous_identical_creates_return_one_template(self):
        barrier = Barrier(2)

        def create():
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                response = self.call("post", self.payload)
                return response.status_code, response.data.get("id")
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: create(), range(2)))
        self.assertEqual(sorted(status for status, _ in results), [200, 201])
        self.assertEqual(len({pk for _, pk in results}), 1)
        self.assertEqual(MessageTemplate.objects.filter(tenant=self.tenant).count(), 1)
        self.assertEqual(MessageTemplateCreateRequest.objects.filter(tenant=self.tenant).count(), 1)


class MessageTemplateCreateRequestMigrationTests(TransactionTestCase):
    def test_expand_preserves_existing_templates_and_old_writer_compatibility(self):
        before = ("messaging", "0043_manualsendrequest")
        after = ("messaging", "0044_message_template_create_request")
        executor = MigrationExecutor(connection)
        if after not in executor.loader.graph.nodes:
            self.skipTest("Requires the real migration graph, not --nomigrations")
        leaf_nodes = executor.loader.graph.leaf_nodes()
        self.addCleanup(lambda: MigrationExecutor(connection).migrate(leaf_nodes))
        executor.migrate([before])
        old_apps = executor.loader.project_state([before]).apps
        OldTenant = old_apps.get_model("core", "Tenant")
        OldTemplate = old_apps.get_model("messaging", "MessageTemplate")
        tenant = OldTenant.objects.create(code="template-create-migration", name="Migration")
        original = OldTemplate.objects.create(tenant=tenant, name="Existing authored template", body="Keep exact body",
            subject="Keep subject", category="grades", is_user_default=True)
        snapshot = OldTemplate.objects.filter(pk=original.pk).values().get()
        executor = MigrationExecutor(connection)
        executor.migrate([after])
        new_apps = executor.loader.project_state([after]).apps
        NewTemplate = new_apps.get_model("messaging", "MessageTemplate")
        Receipt = new_apps.get_model("messaging", "MessageTemplateCreateRequest")
        self.assertEqual(NewTemplate.objects.filter(pk=original.pk).values().get(), snapshot)
        self.assertFalse(Receipt.objects.exists())
        # An old runtime has no receipt model and still writes the unchanged table.
        legacy = OldTemplate.objects.create(tenant=tenant, name="Old writer after expand", body="Legacy body")
        self.assertEqual(NewTemplate.objects.get(pk=legacy.pk).body, "Legacy body")
        self.assertFalse(Receipt.objects.exists())
