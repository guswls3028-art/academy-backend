from __future__ import annotations

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.domains.messaging.default_templates import get_default_templates
from apps.domains.messaging.models import (
    AutoSendConfig, DefaultTemplateSuppression, MessageTemplate,
)
from apps.domains.messaging.views.config_views import (
    AutoSendConfigView,
    ProvisionDefaultTemplatesView,
)
from apps.domains.messaging.views.template_views import (
    MessageTemplateDetailView, MessageTemplateListCreateView,
)


User = get_user_model()


class ProvisionDefaultTemplatesTests(TestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.tenant = Tenant.objects.create(
            code="msg-provision",
            name="Msg Provision",
            is_active=True,
        )
        self.owner_settings = override_settings(OWNER_TENANT_ID=self.tenant.id)
        self.owner_settings.enable()
        self.addCleanup(self.owner_settings.disable)
        self.user = User.objects.create_user(
            username="msg-provision-owner",
            password="test1234",
            tenant=self.tenant,
        )
        TenantMembership.ensure_active(tenant=self.tenant, user=self.user, role="owner")

    def _request(self, method: str, path: str, data=None):
        request = getattr(self.factory, method)(path, data=data or {}, format="json")
        force_authenticate(request, user=self.user)
        request.user = self.user
        request.tenant = self.tenant
        return request

    def test_autosend_get_returns_disabled_virtual_rows_without_writing(self):
        template_count = MessageTemplate.objects.filter(tenant=self.tenant).count()
        config_count = AutoSendConfig.objects.filter(tenant=self.tenant).count()

        response = AutoSendConfigView.as_view()(
            self._request("get", "/api/v1/messaging/auto-send/")
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data)
        self.assertTrue(all(item["id"] is None for item in response.data))
        self.assertTrue(all(item["enabled"] is False for item in response.data))
        self.assertEqual(
            MessageTemplate.objects.filter(tenant=self.tenant).count(),
            template_count,
        )
        self.assertEqual(
            AutoSendConfig.objects.filter(tenant=self.tenant).count(),
            config_count,
        )

    def test_existing_template_body_and_subject_are_not_overwritten(self):
        defaults = get_default_templates(self.tenant.name)
        default = defaults["registration_approved_student"]
        template = MessageTemplate.objects.create(
            tenant=self.tenant,
            name=default["name"],
            category=default["category"],
            subject="원장님이 바꾼 제목",
            body="원장님이 바꾼 본문",
            is_system=True,
        )

        response = ProvisionDefaultTemplatesView.as_view()(
            self._request("post", "/api/v1/messaging/provision-defaults/")
        )

        self.assertEqual(response.status_code, 200)
        template.refresh_from_db()
        self.assertEqual(template.subject, "원장님이 바꾼 제목")
        self.assertEqual(template.body, "원장님이 바꾼 본문")

    def test_provision_defaults_enables_clinic_checkout_with_editable_copy(self):
        response = ProvisionDefaultTemplatesView.as_view()(
            self._request("post", "/api/v1/messaging/provision-defaults/")
        )

        self.assertEqual(response.status_code, 200)
        config = AutoSendConfig.objects.select_related("template").get(
            tenant=self.tenant,
            trigger="clinic_check_out",
        )
        self.assertTrue(config.enabled)
        self.assertEqual(config.message_mode, "alimtalk")
        self.assertEqual(config.template.category, "clinic")
        self.assertIn("하원", config.template.body)

    def test_existing_editable_template_is_not_locked_as_system(self):
        defaults = get_default_templates(self.tenant.name)
        default = defaults["registration_approved_student"]
        template = MessageTemplate.objects.create(
            tenant=self.tenant,
            name=default["name"],
            category=default["category"],
            subject="직접 수정한 제목",
            body="직접 수정한 본문",
            is_system=False,
        )

        response = ProvisionDefaultTemplatesView.as_view()(
            self._request("post", "/api/v1/messaging/provision-defaults/")
        )

        self.assertEqual(response.status_code, 200)
        template.refresh_from_db()
        self.assertFalse(template.is_system)
        self.assertEqual(template.subject, "직접 수정한 제목")
        self.assertEqual(template.body, "직접 수정한 본문")

    def test_deleted_freeform_default_stays_absent_until_selected_restore(self):
        provision = "/api/v1/messaging/provision-defaults/"
        first = ProvisionDefaultTemplatesView.as_view()(self._request("post", provision))
        self.assertEqual(first.status_code, 200)
        name = get_default_templates(self.tenant.name)["freeform_general"]["name"]
        template = MessageTemplate.objects.get(tenant=self.tenant, name=name)
        self.assertTrue(template.is_system)

        deleted = MessageTemplateDetailView.as_view()(
            self._request("delete", f"/api/v1/messaging/templates/{template.id}/"),
            pk=template.id,
        )
        self.assertEqual(deleted.status_code, 204)
        self.assertTrue(DefaultTemplateSuppression.objects.filter(
            tenant=self.tenant, default_key="freeform_general",
        ).exists())
        second = ProvisionDefaultTemplatesView.as_view()(self._request("post", provision))
        self.assertEqual(second.status_code, 200)
        self.assertFalse(MessageTemplate.objects.filter(tenant=self.tenant, name=name).exists())
        self.assertIn("freeform_general", {
            item["key"] for item in second.data["suppressed_defaults"]
        })

        readback = ProvisionDefaultTemplatesView.as_view()(self._request("get", provision))
        self.assertEqual(readback.status_code, 200)
        self.assertIn("freeform_general", {
            item["key"] for item in readback.data["suppressed_defaults"]
        })
        restored = ProvisionDefaultTemplatesView.as_view()(
            self._request("post", provision, {"restore_keys": ["freeform_general"]})
        )
        self.assertEqual(restored.status_code, 200)
        self.assertEqual(restored.data["created_templates"], 1)
        self.assertTrue(MessageTemplate.objects.filter(tenant=self.tenant, name=name).exists())
        self.assertFalse(DefaultTemplateSuppression.objects.filter(
            tenant=self.tenant, default_key="freeform_general",
        ).exists())

    def test_selected_restore_creates_only_requested_default(self):
        provision = "/api/v1/messaging/provision-defaults/"
        response = ProvisionDefaultTemplatesView.as_view()(
            self._request("post", provision, {"restore_keys": ["clinic_reminder"]})
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["created_templates"], 1)
        self.assertEqual(response.data["created_configs"], 1)
        expected_name = get_default_templates(self.tenant.name)["clinic_reminder"]["name"]
        self.assertEqual(list(MessageTemplate.objects.filter(tenant=self.tenant).values_list("name", flat=True)), [expected_name])
        self.assertEqual(list(AutoSendConfig.objects.filter(tenant=self.tenant).values_list("trigger", flat=True)), ["clinic_reminder"])

    def test_linked_template_requires_reassignment_and_keeps_custom_selection(self):
        provision = "/api/v1/messaging/provision-defaults/"
        ProvisionDefaultTemplatesView.as_view()(self._request("post", provision))
        config = AutoSendConfig.objects.select_related("template").get(
            tenant=self.tenant, trigger="clinic_reminder",
        )
        original = config.template
        blocked = MessageTemplateDetailView.as_view()(
            self._request("delete", f"/api/v1/messaging/templates/{original.id}/"),
            pk=original.id,
        )
        self.assertEqual(blocked.status_code, 409)
        self.assertEqual(blocked.data["code"], "auto_send_linked")
        custom = MessageTemplate.objects.create(
            tenant=self.tenant, category="clinic", name="직접 쓴 클리닉 안내",
            body="사용자 본문", is_system=False,
        )
        reassigned = AutoSendConfigView.as_view()(
            self._request("patch", "/api/v1/messaging/auto-send/", {
                "configs": [{"trigger": "clinic_reminder", "template_id": custom.id, "enabled": False}],
            })
        )
        self.assertEqual(reassigned.status_code, 200)
        config.refresh_from_db()
        self.assertEqual(config.template_id, custom.id)
        self.assertFalse(config.enabled)
        deleted = MessageTemplateDetailView.as_view()(
            self._request("delete", f"/api/v1/messaging/templates/{original.id}/"),
            pk=original.id,
        )
        self.assertEqual(deleted.status_code, 204)
        self.assertTrue(DefaultTemplateSuppression.objects.filter(
            tenant=self.tenant, default_key="clinic_reminder",
        ).exists())
        again = ProvisionDefaultTemplatesView.as_view()(self._request("post", provision))
        self.assertEqual(again.status_code, 200)
        config.refresh_from_db()
        self.assertEqual(config.template_id, custom.id)
        self.assertFalse(MessageTemplate.objects.filter(pk=original.id).exists())
        self.assertFalse(MessageTemplate.objects.filter(
            tenant=self.tenant, name=original.name,
        ).exists())
        restored = ProvisionDefaultTemplatesView.as_view()(
            self._request("post", provision, {"restore_keys": ["clinic_reminder"]})
        )
        self.assertEqual(restored.status_code, 200)
        config.refresh_from_db()
        self.assertEqual(config.template_id, custom.id)
        self.assertTrue(MessageTemplate.objects.filter(
            tenant=self.tenant, name=original.name,
        ).exists())

    def test_legacy_absence_and_empty_selection_do_not_recreate_defaults(self):
        provision = "/api/v1/messaging/provision-defaults/"
        config = AutoSendConfig.objects.create(
            tenant=self.tenant, trigger="clinic_reminder", template=None,
            enabled=False, message_mode="alimtalk",
        )
        custom = MessageTemplate.objects.create(
            tenant=self.tenant, category="default", name="사용자 전용", body="본문",
        )
        response = ProvisionDefaultTemplatesView.as_view()(self._request("post", provision))
        self.assertEqual(response.status_code, 200)
        config.refresh_from_db()
        self.assertIsNone(config.template_id)
        self.assertFalse(config.enabled)
        freeform_name = get_default_templates(self.tenant.name)["freeform_general"]["name"]
        self.assertFalse(MessageTemplate.objects.filter(
            tenant=self.tenant, name=freeform_name,
        ).exists())
        self.assertTrue(MessageTemplate.objects.filter(pk=custom.id).exists())
        keys = {item["key"] for item in response.data["suppressed_defaults"]}
        self.assertIn("clinic_reminder", keys)
        self.assertIn("freeform_general", keys)

    def test_delete_projection_and_provider_boundary(self):
        plain = MessageTemplate.objects.create(
            tenant=self.tenant, category="default", name="삭제 가능", body="본문",
        )
        provider = MessageTemplate.objects.create(
            tenant=self.tenant, category="default", name="공급사 승인", body="본문",
            solapi_template_id="SID_APPROVED", solapi_status="APPROVED",
        )
        listed = MessageTemplateListCreateView.as_view()(
            self._request("get", "/api/v1/messaging/templates/")
        )
        self.assertEqual(listed.status_code, 200)
        by_id = {item["id"]: item for item in listed.data}
        self.assertTrue(by_id[plain.id]["can_delete"])
        self.assertEqual(by_id[plain.id]["delete_block_reason"], "")
        self.assertFalse(by_id[provider.id]["can_delete"])
        self.assertEqual(by_id[provider.id]["delete_block_reason"], "provider_bound")
        blocked = MessageTemplateDetailView.as_view()(
            self._request("delete", f"/api/v1/messaging/templates/{provider.id}/"),
            pk=provider.id,
        )
        self.assertEqual(blocked.status_code, 409)
        self.assertTrue(MessageTemplate.objects.filter(pk=provider.id).exists())

    def test_teacher_cannot_delete_system_default_and_other_tenant_is_hidden(self):
        system = MessageTemplate.objects.create(
            tenant=self.tenant, category="default", name="제공 문구", body="본문", is_system=True,
        )
        other_tenant = Tenant.objects.create(code="msg-provision-other", name="Other", is_active=True)
        foreign = MessageTemplate.objects.create(
            tenant=other_tenant, category="default", name="타 학원 문구", body="본문",
        )
        teacher = User.objects.create_user(
            username="msg-provision-teacher", password="test1234", tenant=self.tenant,
        )
        TenantMembership.ensure_active(tenant=self.tenant, user=teacher, role="teacher")
        self.user = teacher
        listed = MessageTemplateListCreateView.as_view()(
            self._request("get", "/api/v1/messaging/templates/")
        )
        self.assertEqual(listed.status_code, 200)
        by_id = {item["id"]: item for item in listed.data}
        self.assertFalse(by_id[system.id]["can_delete"])
        self.assertEqual(by_id[system.id]["delete_block_reason"], "system_permission")
        self.assertNotIn(foreign.id, by_id)
        denied = MessageTemplateDetailView.as_view()(
            self._request("delete", f"/api/v1/messaging/templates/{system.id}/"), pk=system.id,
        )
        self.assertEqual(denied.status_code, 403)
        hidden = MessageTemplateDetailView.as_view()(
            self._request("delete", f"/api/v1/messaging/templates/{foreign.id}/"), pk=foreign.id,
        )
        self.assertEqual(hidden.status_code, 404)
        self.assertTrue(MessageTemplate.objects.filter(pk=system.id).exists())
        self.assertTrue(MessageTemplate.objects.filter(pk=foreign.id).exists())

    def test_restore_keys_validation_does_not_write(self):
        before = MessageTemplate.objects.filter(tenant=self.tenant).count()
        response = ProvisionDefaultTemplatesView.as_view()(
            self._request("post", "/api/v1/messaging/provision-defaults/",
                          {"restore_keys": ["invalid-default"]})
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(MessageTemplate.objects.filter(tenant=self.tenant).count(), before)

    def test_deleted_old_freeform_name_does_not_return_under_new_name(self):
        legacy = MessageTemplate.objects.create(
            tenant=self.tenant, category="default",
            name=f"[{self.tenant.name}] 학원 안내", body="예전 사용자 본문",
        )
        deleted = MessageTemplateDetailView.as_view()(
            self._request("delete", f"/api/v1/messaging/templates/{legacy.id}/"),
            pk=legacy.id,
        )
        self.assertEqual(deleted.status_code, 204)
        self.assertTrue(DefaultTemplateSuppression.objects.filter(
            tenant=self.tenant, default_key="freeform_general",
        ).exists())
        response = ProvisionDefaultTemplatesView.as_view()(
            self._request("post", "/api/v1/messaging/provision-defaults/")
        )
        self.assertEqual(response.status_code, 200)
        current_name = get_default_templates(self.tenant.name)["freeform_general"]["name"]
        self.assertFalse(MessageTemplate.objects.filter(
            tenant=self.tenant, name=current_name,
        ).exists())

    def test_custom_trigger_selection_prevents_unused_default_creation(self):
        custom = MessageTemplate.objects.create(
            tenant=self.tenant, category="clinic", name="선택한 문구", body="사용자 본문",
        )
        config = AutoSendConfig.objects.create(
            tenant=self.tenant, trigger="clinic_reminder", template=custom,
            enabled=True, message_mode="alimtalk",
        )
        response = ProvisionDefaultTemplatesView.as_view()(
            self._request("post", "/api/v1/messaging/provision-defaults/")
        )
        self.assertEqual(response.status_code, 200)
        default_name = get_default_templates(self.tenant.name)["clinic_reminder"]["name"]
        self.assertFalse(MessageTemplate.objects.filter(
            tenant=self.tenant, name=default_name,
        ).exists())
        config.refresh_from_db()
        self.assertEqual(config.template_id, custom.id)

    def test_autosend_patch_enabled_only_preserves_template_and_timing(self):
        template = MessageTemplate.objects.create(
            tenant=self.tenant,
            name="출결 안내",
            category="attendance",
            subject="",
            body="본문",
            is_system=True,
        )
        config = AutoSendConfig.objects.create(
            tenant=self.tenant,
            trigger="check_in_complete",
            template=template,
            enabled=True,
            message_mode="alimtalk",
            minutes_before=30,
        )

        response = AutoSendConfigView.as_view()(
            self._request(
                "patch",
                "/api/v1/messaging/auto-send/",
                {"configs": [{"trigger": "check_in_complete", "enabled": False}]},
            )
        )

        self.assertEqual(response.status_code, 200)
        config.refresh_from_db()
        self.assertFalse(config.enabled)
        self.assertEqual(config.template_id, template.id)
        self.assertEqual(config.minutes_before, 30)

    def test_autosend_patch_template_only_preserves_enabled_and_timing(self):
        old_template = MessageTemplate.objects.create(
            tenant=self.tenant,
            name="기존 안내",
            category="attendance",
            subject="",
            body="기존 본문",
            is_system=True,
        )
        new_template = MessageTemplate.objects.create(
            tenant=self.tenant,
            name="새 안내",
            category="attendance",
            subject="",
            body="새 본문",
            is_system=True,
        )
        config = AutoSendConfig.objects.create(
            tenant=self.tenant,
            trigger="check_in_complete",
            template=old_template,
            enabled=True,
            message_mode="alimtalk",
            minutes_before=30,
        )

        response = AutoSendConfigView.as_view()(
            self._request(
                "patch",
                "/api/v1/messaging/auto-send/",
                {"configs": [{"trigger": "check_in_complete", "template_id": new_template.id}]},
            )
        )

        self.assertEqual(response.status_code, 200, response.data)
        config.refresh_from_db()
        self.assertTrue(config.enabled)
        self.assertEqual(config.template_id, new_template.id)
        self.assertEqual(config.minutes_before, 30)

    def test_teacher_membership_cannot_patch_auto_send_settings(self):
        teacher = User.objects.create_user(
            username="msg-provision-teacher",
            password="test1234",
            tenant=self.tenant,
        )
        TenantMembership.ensure_active(tenant=self.tenant, user=teacher, role="teacher")
        request = self.factory.patch(
            "/api/v1/messaging/auto-send/",
            data={"configs": [{"trigger": "check_in_complete", "enabled": True}]},
            format="json",
        )
        force_authenticate(request, user=teacher)
        request.user = teacher
        request.tenant = self.tenant

        response = AutoSendConfigView.as_view()(request)

        self.assertEqual(response.status_code, 403)
        self.assertFalse(
            AutoSendConfig.objects.filter(
                tenant=self.tenant,
                trigger="check_in_complete",
            ).exists()
        )

    def test_autosend_patch_parses_string_false_without_enabling(self):
        config = AutoSendConfig.objects.create(
            tenant=self.tenant,
            trigger="check_in_complete",
            enabled=True,
            message_mode="alimtalk",
        )

        response = AutoSendConfigView.as_view()(
            self._request(
                "patch",
                "/api/v1/messaging/auto-send/",
                {"configs": [{"trigger": "check_in_complete", "enabled": "false"}]},
            )
        )

        self.assertEqual(response.status_code, 200)
        config.refresh_from_db()
        self.assertFalse(config.enabled)

    def test_autosend_patch_parses_show_actual_time_string_false(self):
        template = MessageTemplate.objects.create(
            tenant=self.tenant,
            name="Clinic check-in content",
            category=MessageTemplate.Category.CLINIC,
            body="Clinic check-in",
        )
        config = AutoSendConfig.objects.create(
            tenant=self.tenant,
            trigger="clinic_check_in",
            enabled=True,
            message_mode="alimtalk",
            show_actual_time=True,
            template=template,
        )

        response = AutoSendConfigView.as_view()(
            self._request(
                "patch",
                "/api/v1/messaging/auto-send/",
                {"configs": [{"trigger": "clinic_check_in", "show_actual_time": "false"}]},
            )
        )

        self.assertEqual(response.status_code, 200)
        config.refresh_from_db()
        self.assertFalse(config.show_actual_time)
        self.assertTrue(config.enabled)

    def test_autosend_patch_keeps_auto_send_channel_alimtalk_only(self):
        template = MessageTemplate.objects.create(
            tenant=self.tenant,
            name="Clinic channel content",
            category=MessageTemplate.Category.CLINIC,
            body="Clinic channel",
        )
        config = AutoSendConfig.objects.create(
            tenant=self.tenant,
            trigger="clinic_check_in",
            enabled=True,
            message_mode="alimtalk",
            template=template,
        )

        response = AutoSendConfigView.as_view()(
            self._request(
                "patch",
                "/api/v1/messaging/auto-send/",
                {"configs": [{"trigger": "clinic_check_in", "message_mode": "sms"}]},
            )
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("message_mode", response.data)
        config.refresh_from_db()
        self.assertEqual(config.message_mode, "alimtalk")
        self.assertTrue(config.enabled)

    def test_autosend_patch_rejects_invalid_scheduled_hour_value(self):
        config = AutoSendConfig.objects.create(
            tenant=self.tenant,
            trigger="clinic_check_in",
            enabled=True,
            message_mode="alimtalk",
            delay_mode="immediate",
        )

        response = AutoSendConfigView.as_view()(
            self._request(
                "patch",
                "/api/v1/messaging/auto-send/",
                {
                    "configs": [
                        {
                            "trigger": "clinic_check_in",
                            "delay_mode": "scheduled_hour",
                            "delay_value": 24,
                        }
                    ]
                },
            )
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("delay_value", response.data)
        config.refresh_from_db()
        self.assertEqual(config.delay_mode, "immediate")
        self.assertIsNone(config.delay_value)

    def test_autosend_patch_preserves_valid_delay_minutes(self):
        template = MessageTemplate.objects.create(
            tenant=self.tenant,
            name="Clinic delay content",
            category=MessageTemplate.Category.CLINIC,
            body="Clinic delay",
        )
        config = AutoSendConfig.objects.create(
            tenant=self.tenant,
            trigger="clinic_check_in",
            enabled=True,
            message_mode="alimtalk",
            delay_mode="immediate",
            template=template,
        )

        response = AutoSendConfigView.as_view()(
            self._request(
                "patch",
                "/api/v1/messaging/auto-send/",
                {
                    "configs": [
                        {
                            "trigger": "clinic_check_in",
                            "delay_mode": "delay_minutes",
                            "delay_value": "30",
                        }
                    ]
                },
            )
        )

        self.assertEqual(response.status_code, 200)
        config.refresh_from_db()
        self.assertEqual(config.delay_mode, "delay_minutes")
        self.assertEqual(config.delay_value, 30)

    def test_autosend_patch_rejects_invalid_delay_mode(self):
        config = AutoSendConfig.objects.create(
            tenant=self.tenant,
            trigger="clinic_check_in",
            enabled=True,
            message_mode="alimtalk",
            delay_mode="immediate",
        )

        response = AutoSendConfigView.as_view()(
            self._request(
                "patch",
                "/api/v1/messaging/auto-send/",
                {"configs": [{"trigger": "clinic_check_in", "delay_mode": "legacy_mode", "delay_value": 10}]},
            )
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("delay_mode", response.data)
        config.refresh_from_db()
        self.assertEqual(config.delay_mode, "immediate")
        self.assertIsNone(config.delay_value)

    def test_autosend_patch_rejects_negative_delay_value(self):
        config = AutoSendConfig.objects.create(
            tenant=self.tenant,
            trigger="clinic_check_in",
            enabled=True,
            message_mode="alimtalk",
            delay_mode="immediate",
        )

        response = AutoSendConfigView.as_view()(
            self._request(
                "patch",
                "/api/v1/messaging/auto-send/",
                {"configs": [{"trigger": "clinic_check_in", "delay_mode": "delay_minutes", "delay_value": -1}]},
            )
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("delay_value", response.data)
        config.refresh_from_db()
        self.assertEqual(config.delay_mode, "immediate")
        self.assertIsNone(config.delay_value)

    def test_autosend_patch_rejects_scheduled_hour_mode_without_valid_hour(self):
        config = AutoSendConfig.objects.create(
            tenant=self.tenant,
            trigger="clinic_check_in",
            enabled=True,
            message_mode="alimtalk",
            delay_mode="delay_minutes",
            delay_value=30,
        )

        response = AutoSendConfigView.as_view()(
            self._request(
                "patch",
                "/api/v1/messaging/auto-send/",
                {"configs": [{"trigger": "clinic_check_in", "delay_mode": "scheduled_hour"}]},
            )
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("delay_value", response.data)
        config.refresh_from_db()
        self.assertEqual(config.delay_mode, "delay_minutes")
        self.assertEqual(config.delay_value, 30)

    def test_autosend_patch_rejects_delay_mode_change_without_value_even_if_old_value_is_valid_hour(self):
        config = AutoSendConfig.objects.create(
            tenant=self.tenant,
            trigger="clinic_check_in",
            enabled=True,
            message_mode="alimtalk",
            delay_mode="delay_minutes",
            delay_value=10,
        )

        response = AutoSendConfigView.as_view()(
            self._request(
                "patch",
                "/api/v1/messaging/auto-send/",
                {"configs": [{"trigger": "clinic_check_in", "delay_mode": "scheduled_hour"}]},
            )
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("delay_value", response.data)
        config.refresh_from_db()
        self.assertEqual(config.delay_mode, "delay_minutes")
        self.assertEqual(config.delay_value, 10)

    def test_autosend_patch_rejects_invalid_minutes_before_without_clearing(self):
        config = AutoSendConfig.objects.create(
            tenant=self.tenant,
            trigger="clinic_check_in",
            enabled=True,
            message_mode="alimtalk",
            minutes_before=20,
        )

        response = AutoSendConfigView.as_view()(
            self._request(
                "patch",
                "/api/v1/messaging/auto-send/",
                {"configs": [{"trigger": "clinic_check_in", "minutes_before": "soon"}]},
            )
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("minutes_before", response.data)
        config.refresh_from_db()
        self.assertEqual(config.minutes_before, 20)

    def test_autosend_patch_rejects_missing_template_without_clearing(self):
        template = MessageTemplate.objects.create(
            tenant=self.tenant,
            name="출결 템플릿",
            category="attendance",
            subject="",
            body="본문",
            is_system=True,
        )
        config = AutoSendConfig.objects.create(
            tenant=self.tenant,
            trigger="clinic_check_in",
            template=template,
            enabled=True,
            message_mode="alimtalk",
        )

        response = AutoSendConfigView.as_view()(
            self._request(
                "patch",
                "/api/v1/messaging/auto-send/",
                {"configs": [{"trigger": "clinic_check_in", "template_id": 999999}]},
            )
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("template_id", response.data)
        config.refresh_from_db()
        self.assertEqual(config.template_id, template.id)

    def test_autosend_patch_invalid_later_item_rolls_back_earlier_save(self):
        template = MessageTemplate.objects.create(
            tenant=self.tenant,
            name="출결 템플릿",
            category="attendance",
            subject="",
            body="본문",
            is_system=True,
        )
        config = AutoSendConfig.objects.create(
            tenant=self.tenant,
            trigger="clinic_check_in",
            template=template,
            enabled=True,
            message_mode="alimtalk",
        )

        response = AutoSendConfigView.as_view()(
            self._request(
                "patch",
                "/api/v1/messaging/auto-send/",
                {
                    "configs": [
                        {"trigger": "clinic_check_in", "enabled": False},
                        {"trigger": "check_in_complete", "template_id": 999999},
                    ]
                },
            )
        )

        self.assertEqual(response.status_code, 400)
        config.refresh_from_db()
        self.assertTrue(config.enabled)
        self.assertFalse(
            AutoSendConfig.objects.filter(
                tenant=self.tenant,
                trigger="check_in_complete",
            ).exists()
        )

    def test_autosend_patch_invalid_new_config_does_not_leave_default_row(self):
        response = AutoSendConfigView.as_view()(
            self._request(
                "patch",
                "/api/v1/messaging/auto-send/",
                {"configs": [{"trigger": "check_in_complete", "template_id": 999999}]},
            )
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(
            AutoSendConfig.objects.filter(
                tenant=self.tenant,
                trigger="check_in_complete",
            ).exists()
        )

    def test_autosend_patch_cannot_change_another_tenant_preference(self):
        other_tenant = Tenant.objects.create(
            code="msg-provision-other",
            name="Msg Provision Other",
            is_active=True,
        )
        own_config = AutoSendConfig.objects.create(
            tenant=self.tenant,
            trigger="clinic_reminder",
            enabled=True,
            message_mode="alimtalk",
        )
        other_config = AutoSendConfig.objects.create(
            tenant=other_tenant,
            trigger="clinic_reminder",
            enabled=True,
            message_mode="alimtalk",
        )

        response = AutoSendConfigView.as_view()(
            self._request(
                "patch",
                "/api/v1/messaging/auto-send/",
                {"configs": [{"trigger": "clinic_reminder", "enabled": False}]},
            )
        )

        self.assertEqual(response.status_code, 200)
        own_config.refresh_from_db()
        other_config.refresh_from_db()
        self.assertFalse(own_config.enabled)
        self.assertTrue(other_config.enabled)

    def test_community_answer_triggers_are_not_enabled_by_default(self):
        response = AutoSendConfigView.as_view()(
            self._request("get", "/api/v1/messaging/auto-send/")
        )

        self.assertEqual(response.status_code, 200)
        by_trigger = {item["trigger"]: item for item in response.data}
        self.assertFalse(by_trigger["matchup_report_submitted"]["enabled"])
        self.assertFalse(by_trigger["qna_answered"]["enabled"])
        self.assertFalse(by_trigger["counsel_answered"]["enabled"])
        self.assertFalse(AutoSendConfig.objects.filter(tenant=self.tenant).exists())

    def test_explicit_provision_creates_disabled_qna_template_contract(self):
        response = ProvisionDefaultTemplatesView.as_view()(
            self._request("post", "/api/v1/messaging/provision-defaults/")
        )

        self.assertEqual(response.status_code, 200)
        qna_config = AutoSendConfig.objects.select_related("template").get(
            tenant=self.tenant,
            trigger="qna_answered",
        )
        self.assertFalse(qna_config.enabled)
        qna_template = qna_config.template
        self.assertEqual(qna_template.subject, "")
        self.assertIn("[질문 답변 완료]", qna_template.body)
        self.assertIn("#{학생이름2}", qna_template.body)
        self.assertIn("#{사이트링크}", qna_template.body)
        self.assertNotIn("#{선생님메모}", qna_template.body)

    def test_video_encoding_trigger_is_not_enabled_by_default_until_template_ready(self):
        response = AutoSendConfigView.as_view()(
            self._request("get", "/api/v1/messaging/auto-send/")
        )

        self.assertEqual(response.status_code, 200)
        by_trigger = {item["trigger"]: item for item in response.data}
        self.assertFalse(by_trigger["video_encoding_complete"]["enabled"])
        self.assertFalse(AutoSendConfig.objects.filter(tenant=self.tenant).exists())

    def test_autosend_patch_rejects_enable_without_effective_approved_template(self):
        template = MessageTemplate.objects.create(
            tenant=self.tenant,
            name="상담 답변",
            category="community",
            subject="",
            body="본문",
            solapi_template_id="",
            solapi_status="",
        )

        response = AutoSendConfigView.as_view()(
            self._request(
                "patch",
                "/api/v1/messaging/auto-send/",
                {
                    "configs": [
                        {
                            "trigger": "counsel_answered",
                            "template_id": template.id,
                            "enabled": True,
                        }
                    ]
                },
            )
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["trigger"], "counsel_answered")
        self.assertIn("template_id", response.data)
        self.assertFalse(
            AutoSendConfig.objects.filter(
                tenant=self.tenant,
                trigger="counsel_answered",
            ).exists()
        )

    def test_autosend_patch_allows_enable_with_approved_tenant_template(self):
        template = MessageTemplate.objects.create(
            tenant=self.tenant,
            name="상담 답변",
            category="community",
            subject="",
            body="본문",
            solapi_template_id="KA01TP_APPROVED",
            solapi_status="APPROVED",
        )

        response = AutoSendConfigView.as_view()(
            self._request(
                "patch",
                "/api/v1/messaging/auto-send/",
                {
                    "configs": [
                        {
                            "trigger": "counsel_answered",
                            "template_id": template.id,
                            "enabled": True,
                        }
                    ]
                },
            )
        )

        self.assertEqual(response.status_code, 200)
        config = AutoSendConfig.objects.get(
            tenant=self.tenant,
            trigger="counsel_answered",
        )
        self.assertTrue(config.enabled)
        self.assertEqual(config.template_id, template.id)

    def test_provision_defaults_does_not_auto_submit_kakao_template_review(self):
        self.tenant.kakao_pfid = "KA01PF"
        self.tenant.own_solapi_api_key = "key"
        self.tenant.own_solapi_api_secret = "secret"
        self.tenant.save(
            update_fields=["kakao_pfid", "own_solapi_api_key", "own_solapi_api_secret"]
        )

        with patch(
            "apps.domains.messaging.solapi_template_client.create_kakao_template"
        ) as mocked_create:
            response = ProvisionDefaultTemplatesView.as_view()(
                self._request("post", "/api/v1/messaging/provision-defaults/")
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["submitted_reviews"], 0)
        self.assertEqual(response.data["review_note"], "")
        mocked_create.assert_not_called()
