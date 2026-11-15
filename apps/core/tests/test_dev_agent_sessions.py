from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, SimpleTestCase, override_settings
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Tenant, TenantMembership
from apps.core.services.agent_sessions import normalize_snapshot
from apps.core.views.dev_agent_sessions import DevAgentSessionsView


def snapshot():
    return {"schema": 1, "collected_at": datetime.now(timezone.utc).isoformat(), "nodes": [
        {"id": "codex:parent", "provider": "codex", "parent_id": None, "title": "검토", "role": "세션", "status": "running", "evidence": "last_turn", "updated_at": None},
        {"id": "codex:child", "provider": "codex", "parent_id": "codex:parent", "title": "디자인 확인", "role": "reviewer", "status": "completed", "evidence": "last_turn", "updated_at": None},
    ]}


class AgentSessionSnapshotTests(SimpleTestCase):
    def test_whitelists_metadata_and_keeps_verified_hierarchy(self):
        raw = snapshot()
        raw["transcript"] = "do not export"
        raw["nodes"][1]["prompt"] = "do not export"
        raw["nodes"][1]["title"] = "API_KEY=private-secret-value"
        data = normalize_snapshot(raw)
        self.assertEqual(data["source_status"], "connected")
        self.assertEqual(data["nodes"][1]["parent_id"], "codex:parent")
        self.assertEqual(data["nodes"][1]["title"], "[비공개]")
        self.assertNotIn("do not export", str(data))

    def test_disconnected_collection_retains_last_known_data_as_stale(self):
        raw = snapshot()
        raw["collected_at"] = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()
        data = normalize_snapshot(raw)
        self.assertEqual(data["source_status"], "stale")
        self.assertEqual(len(data["nodes"]), 2)

    def test_missing_parent_is_not_reassigned_as_a_root(self):
        raw = snapshot()
        raw["nodes"] = raw["nodes"][1:]
        self.assertEqual(normalize_snapshot(raw)["nodes"][0]["parent_id"], "codex:parent")

    def test_refuses_duplicate_cyclic_oversized_and_future_snapshots(self):
        duplicate = snapshot()
        duplicate["nodes"].append(duplicate["nodes"][0])
        cyclic = snapshot()
        cyclic["nodes"][0]["parent_id"] = "codex:child"
        oversized = snapshot()
        oversized["nodes"] *= 301
        future = snapshot()
        future["collected_at"] = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
        for raw in (duplicate, cyclic, oversized, future):
            with self.subTest(raw=raw["collected_at"]), self.assertRaises(ValueError):
                normalize_snapshot(raw)

    def test_invalid_source_flags_and_cross_provider_hierarchy_are_rejected(self):
        for field, value in (("schema", True), ("source_errors", None), ("source_errors", ["other"]), ("truncated", "false")):
            raw = {**snapshot(), field: value}
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                normalize_snapshot(raw)
        raw = snapshot()
        raw["nodes"][1]["parent_id"] = "claude:other"
        with self.assertRaises(ValueError):
            normalize_snapshot(raw)


@override_settings(OWNER_TENANT_ID=1)
class AgentSessionPermissionTests(TestCase):
    def setUp(self):
        self.platform = Tenant.objects.create(id=1, code="qa-agent-platform", name="QA platform")
        self.other = Tenant.objects.create(code="qa-agent-other", name="QA other")
        self.viewer = self.user("t1_admin97", self.platform, "owner", superuser=True)
        self.factory = APIRequestFactory()

    def user(self, username, tenant, role, *, superuser=False):
        user = get_user_model().objects.create_user(username=username, tenant=tenant, is_staff=role in {"owner", "admin"}, is_superuser=superuser)
        TenantMembership.ensure_active(tenant=tenant, user=user, role=role)
        return user

    def request(self, user, tenant=None, method="get", token=None):
        request = getattr(self.factory, method)("/api/v1/core/dev/agent-sessions/")
        request.tenant = tenant or self.platform
        if user:
            force_authenticate(request, user=user, token=token)
        return DevAgentSessionsView.as_view()(request)

    @patch("apps.core.views.dev_agent_sessions.load_snapshot")
    def test_exact_owner_can_read_without_mutation_and_response_is_not_cacheable(self, load):
        load.return_value = normalize_snapshot(snapshot())
        response = self.request(self.viewer)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertEqual(len(response.data["nodes"]), 2)
        self.assertEqual(self.request(self.viewer, method="post").status_code, 405)
        self.assertEqual(self.request(self.viewer, method="delete").status_code, 405)
        self.assertEqual(load.call_count, 1)

    @patch("apps.core.views.dev_agent_sessions.load_snapshot")
    def test_other_accounts_tenants_and_support_sessions_cannot_load_metadata(self, load):
        other_owner = self.user("other-owner", self.platform, "owner", superuser=True)
        same_login_other_tenant = self.user("t2_admin97", self.other, "owner", superuser=True)
        TenantMembership.ensure_active(tenant=self.platform, user=same_login_other_tenant, role="owner")
        student = self.user("student", self.platform, "student")
        for user, tenant, token in [
            (other_owner, self.platform, None),
            (same_login_other_tenant, self.platform, None),
            (self.viewer, self.other, None),
            (student, self.platform, None),
            (self.viewer, self.platform, {"impersonated_by": other_owner.id}),
            (self.viewer, self.platform, {"support_preview": True}),
        ]:
            with self.subTest(user=user.username, tenant=tenant.id, token=token):
                self.assertEqual(self.request(user, tenant, token=token).status_code, 403)
        self.assertIn(self.request(None).status_code, {401, 403})
        load.assert_not_called()

    @patch("apps.core.views.dev_agent_sessions.load_snapshot", side_effect=ValueError("secret storage diagnostic"))
    def test_source_failure_is_visible_without_exposing_diagnostics(self, load):
        response = self.request(self.viewer)
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("secret", str(response.data))
        self.assertEqual(response["Cache-Control"], "no-store")
