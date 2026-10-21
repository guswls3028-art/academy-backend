from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import resolve, reverse
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.domains.messaging.models import MessageTemplate


class MessageTemplateConcurrencyTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = Tenant.objects.create(code="template-cas", name="Template CAS")
        self.owner = get_user_model().objects.create_user(
            username="template-cas-owner", tenant=self.tenant, is_staff=True,
        )
        TenantMembership.ensure_active(tenant=self.tenant, user=self.owner, role="owner")
        self.template = MessageTemplate.objects.create(
            tenant=self.tenant, category="grades", name="Shared grades", subject="Original",
            body="Original #{학생이름3}", is_user_default=True,
        )
        self.path = reverse("messaging-template-detail", kwargs={"pk": self.template.pk})

    def request(self, method, data=None, *, user=None, tenant=None, path=None, anonymous=False):
        path = path or self.path
        request = getattr(self.factory, method)(path, data=data or {}, format="json")
        request.tenant = tenant or self.tenant
        if not anonymous:
            force_authenticate(request, user=user or self.owner)
        return resolve(path).func(request, **resolve(path).kwargs)

    def read(self):
        response = self.request("get")
        self.assertEqual(response.status_code, 200)
        return response.data

    def test_stale_editor_cannot_overwrite_another_saved_shared_template(self):
        editor_a = self.read()
        editor_b = self.read()
        saved = self.request("patch", {
            "body": "A saved #{다음차시}", "expected_updated_at": editor_a["updated_at"],
        })
        self.assertEqual(saved.status_code, 200)
        stale = self.request("patch", {
            "body": "B stale draft", "expected_updated_at": editor_b["updated_at"],
        })
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(stale.data["code"], "template_conflict")
        self.assertEqual(stale.data["current_template"]["body"], "A saved #{다음차시}")
        self.assertEqual(stale.data["current_template"]["updated_at"], saved.data["updated_at"])
        self.template.refresh_from_db()
        self.assertEqual(self.template.body, "A saved #{다음차시}")
        self.assertTrue(self.template.is_user_default)
    def test_same_content_retry_after_lost_response_preserves_timestamp_and_default(self):
        observed = self.read()
        payload = {"body": "Committed body", "subject": "Committed subject",
                   "expected_updated_at": observed["updated_at"]}
        first = self.request("patch", payload)
        self.assertEqual(first.status_code, 200)
        retry = self.request("patch", payload)
        self.assertEqual(retry.status_code, 200)
        self.assertEqual(retry.data["updated_at"], first.data["updated_at"])
        self.assertEqual(retry.data["body"], "Committed body")
        self.assertTrue(retry.data["is_user_default"])
        self.assertNotIn("expected_updated_at", retry.data)

    def test_explicit_current_snapshot_allows_draft_reapply_after_conflict(self):
        observed = self.read()
        self.assertEqual(self.request("patch", {"body": "Other editor"}).status_code, 200)
        stale = self.request("patch", {"body": "Preserved draft", "expected_updated_at": observed["updated_at"]})
        self.assertEqual(stale.status_code, 409)
        recovered = self.request("patch", {"body": "Preserved draft",
            "expected_updated_at": stale.data["current_template"]["updated_at"]})
        self.assertEqual(recovered.status_code, 200)
        self.assertEqual(self.read()["body"], "Preserved draft")

    def test_legacy_patch_without_condition_remains_supported(self):
        observed = self.read()
        self.assertEqual(self.request("patch", {"body": "First edit"}).status_code, 200)
        saved = self.request("patch", {"subject": "Legacy client edit"})
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.data["body"], "First edit")
        self.assertEqual(saved.data["subject"], "Legacy client edit")
        self.assertNotEqual(saved.data["updated_at"], observed["updated_at"])

    def test_stale_default_and_category_change_preserves_both_categories(self):
        other_default = MessageTemplate.objects.create(
            tenant=self.tenant, category="attendance", name="Attendance", body="Attendance", is_user_default=True,
        )
        observed = self.read()
        self.request("patch", {"body": "New shared body"})
        stale = self.request("patch", {"category": "attendance", "is_user_default": True,
            "expected_updated_at": observed["updated_at"]})
        self.assertEqual(stale.status_code, 409)
        self.template.refresh_from_db()
        other_default.refresh_from_db()
        self.assertEqual(self.template.category, "grades")
        self.assertTrue(self.template.is_user_default)
        self.assertTrue(other_default.is_user_default)
        current = self.read()
        moved = self.request("patch", {"category": "attendance", "is_user_default": True,
            "expected_updated_at": current["updated_at"]})
        self.assertEqual(moved.status_code, 200)
        other_default.refresh_from_db()
        self.assertFalse(other_default.is_user_default)

    def test_invalid_conditional_timestamp_rejected_without_writing(self):
        original = self.read()
        for value in [None, "", "not-a-date", True, {"invalid": "value"}]:
            with self.subTest(value=value):
                response = self.request("patch", {"body": "Rejected", "expected_updated_at": value})
                self.assertEqual(response.status_code, 400)
        self.assertEqual(self.read()["body"], original["body"])
        self.assertEqual(self.read()["updated_at"], original["updated_at"])

    def test_foreign_template_id_does_not_return_current_template(self):
        foreign = Tenant.objects.create(code="template-cas-other", name="Other")
        template = MessageTemplate.objects.create(tenant=foreign, name="Private", body="Private tenant text")
        response = self.request("patch", {"body": "Rejected", "expected_updated_at": self.read()["updated_at"]},
            path=reverse("messaging-template-detail", kwargs={"pk": template.pk}))
        self.assertEqual(response.status_code, 404)
        self.assertNotIn("current_template", response.data)
        template.refresh_from_db()
        self.assertEqual(template.body, "Private tenant text")

    def test_foreign_staff_and_nonstaff_roles_cannot_get_conflict_snapshot(self):
        foreign = Tenant.objects.create(code="template-cas-foreign", name="Foreign")
        for role in ["parent", "student", "foreign_staff"]:
            with self.subTest(role=role):
                tenant = foreign if role == "foreign_staff" else self.tenant
                user = get_user_model().objects.create_user(username=f"template-cas-{role}", tenant=tenant,
                    is_staff=role == "foreign_staff")
                TenantMembership.ensure_active(tenant=tenant, user=user,
                    role="staff" if role == "foreign_staff" else role)
                response = self.request("patch", {"body": "Rejected", "expected_updated_at": self.read()["updated_at"]}, user=user)
                self.assertEqual(response.status_code, 403)
                self.assertNotIn("current_template", response.data)
        self.assertEqual(self.read()["body"], "Original #{학생이름3}")

    def test_unauthenticated_and_system_template_guards_still_precede_conflict(self):
        response = self.request("patch", {"body": "Rejected"}, anonymous=True)
        self.assertIn(response.status_code, [401, 403])
        self.template.is_system = True
        self.template.save(update_fields=["is_system"])
        response = self.request("patch", {"body": "Rejected", "expected_updated_at": self.read()["updated_at"]})
        self.assertEqual(response.status_code, 403)
        self.assertNotIn("current_template", response.data)
        self.assertEqual(self.read()["body"], "Original #{학생이름3}")

    def test_condition_cannot_be_passed_to_create_as_a_model_field(self):
        response = self.request("post", {"name": "Invalid conditional creation", "body": "Draft",
            "expected_updated_at": self.read()["updated_at"]}, path=reverse("messaging-templates"))
        self.assertEqual(response.status_code, 400)
        self.assertFalse(MessageTemplate.objects.filter(tenant=self.tenant, name="Invalid conditional creation").exists())
    def test_body_only_retry_preserves_a_later_subject_edit(self):
        observed = self.read()
        payload = {"body": "Committed body only", "expected_updated_at": observed["updated_at"]}
        self.assertEqual(self.request("patch", payload).status_code, 200)
        newer = self.request("patch", {"subject": "A later subject"})
        self.assertEqual(newer.status_code, 200)
        retry = self.request("patch", payload)
        self.assertEqual(retry.status_code, 200)
        self.assertEqual(retry.data["subject"], "A later subject")
        self.assertEqual(retry.data["updated_at"], newer.data["updated_at"])

    def test_delete_between_initial_lookup_and_locked_update_returns_404(self):
        from apps.domains.messaging.serializers import MessageTemplateSerializer

        observed = self.read()
        original_update = MessageTemplateSerializer.update
        template_id = self.template.pk

        def delete_before_locked_reread(serializer, instance, validated_data):
            # The view already found and authorized this instance. Model the
            # completed concurrent DELETE before update takes the tenant lock.
            MessageTemplate.objects.filter(pk=template_id).delete()
            return original_update(serializer, instance, validated_data)

        with patch.object(MessageTemplateSerializer, "update", delete_before_locked_reread):
            response = self.request("patch", {
                "body": "Must not resurrect", "expected_updated_at": observed["updated_at"],
            })
        self.assertEqual(response.status_code, 404)
        self.assertNotIn("current_template", response.data)
        self.assertFalse(MessageTemplate.objects.filter(pk=template_id).exists())
