"""Offline tests: no Django bootstrap, credentials store, network or AWS."""
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

PATH = Path(__file__).with_name("probe-account-registration-development.py")
SPEC = importlib.util.spec_from_file_location("account_probe", PATH)
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


class AccountProbeTests(unittest.TestCase):
    def setUp(self):
        self.user = SimpleNamespace(pk=17, tenant_id=71, phone="01087654321", password="private-hash",
                                    account_notice_password_ciphertext="private-ciphertext")
        self.calls = []

    def authenticate(self, raw="4321", kind="phone_last4", role="student", http=None, check=None):
        def transport(path, tenant, **kwargs):
            self.calls.append(path)
            if path.endswith("token/"):
                self.assertEqual(kwargs["data"]["password"], raw)
                return {"access": "private-token"}
            if path.endswith("core/me/"):
                return {"id": 17, "tenantRole": role, "linkedStudents": [{"id": 23}]}
            return {"id": 23}
        return probe.authenticate(self.user, role, 23, "qa-tenant", kind,
                                  recover=lambda *args, **kwargs: raw,
                                  check=check or (lambda raw, encoded: True),
                                  display=lambda user: "synthetic-login", http=http or transport)

    def test_real_token_core_and_student_me_are_required_for_each_role(self):
        for role in ("student", "parent"):
            self.calls.clear()
            self.assertTrue(self.authenticate(role=role))
            self.assertEqual(self.calls, ["/api/v1/token/", "/api/v1/core/me/", "/api/v1/student/me/"])

    def test_unknown_or_non_matching_raw_fails_without_reset_or_http(self):
        for raw, checker in [("", None), ("4321", lambda *args: False)]:
            with self.assertRaises(probe.ProbeFailure):
                self.authenticate(raw=raw, check=checker)
        self.assertEqual(self.calls, [])

    def test_own_phone_and_random_format_are_checked_before_authentication(self):
        for raw, kind in [("9999", "phone_last4"), ("abc123", "random"), ("12345", "random"), ("1234567", "random")]:
            with self.assertRaises(probe.ProbeFailure):
                self.authenticate(raw=raw, kind=kind)
        self.assertEqual(self.calls, [])
        self.assertTrue(self.authenticate(raw="000123", kind="random"))

    def test_direct_password_preserves_whitespace(self):
        self.assertTrue(self.authenticate(raw=" 1234 ", kind="fixed"))

    def test_token_or_role_failure_cannot_pass(self):
        for payload in [{}, {"access": ""}]:
            with self.assertRaises(probe.ProbeFailure):
                self.authenticate(http=lambda *args, **kwargs: payload)
        def wrong_role(path, *args, **kwargs):
            return {"access": "private-token"} if path.endswith("token/") else {"id": 17, "tenantRole": "owner"}
        with self.assertRaises(probe.ProbeFailure):
            self.authenticate(http=wrong_role)

    def test_loopback_has_no_proxy_redirect_or_arbitrary_path(self):
        with patch.object(probe.urllib.request, "build_opener") as opened:
            with self.assertRaises(probe.ProbeFailure):
                probe.loopback_json("https://production.invalid/api/v1/token/", "qa-tenant")
            opened.assert_not_called()
        with self.assertRaises(probe.ProbeFailure):
            probe.NoRedirect().redirect_request(None, None, 302, "", {}, "https://production.invalid")

    def test_targets_and_modes_are_closed(self):
        for target in [0, -1, True, "17", 2**63]:
            with self.assertRaises(probe.ProbeFailure):
                probe.validate_arguments(target, "verify", "random")
        for mode, kind in [("cleanup", "random"), ("verify", "unknown")]:
            with self.assertRaises(probe.ProbeFailure):
                probe.validate_arguments(17, mode, kind)
        probe.validate_arguments(17, "compare", "fixed")

    def test_snapshot_excludes_only_auth_observation_fields(self):
        fields = [SimpleNamespace(attname=name) for name in ["password", "token_version", "last_login", "updated_at"]]
        user = SimpleNamespace(_meta=SimpleNamespace(concrete_fields=fields), password="secret-hash",
                               token_version=1, last_login=None, updated_at=None)
        before = probe.canonical_state([user])
        user.last_login = "later"
        self.assertEqual(probe.canonical_state([user]), before)
        user.password = "changed"
        self.assertNotEqual(probe.canonical_state([user]), before)

    def test_source_never_uses_mutating_password_resolution_or_returns_secrets(self):
        source = PATH.read_text(encoding="utf-8")
        self.assertNotIn("account_notice_password(", source)
        self.assertNotIn("set_password(", source)
        self.assertNotIn("print(", source)
        result_source = source.split('return {"schema": SCHEMA', 1)[1]
        for forbidden in ['"password"', '"access"', '"refresh"', '"phone"', '"seal"']:
            self.assertNotIn(forbidden, result_source)
        scope = probe.scope_hash("qa-tenant", 71, "release", "digest")
        self.assertRegex(scope, r"^[a-f0-9]{64}$")
        self.assertNotIn("qa-tenant", json.dumps(scope))

    def test_snapshot_compare_checks_exact_hash_and_cleans_only_owned_observation(self):
        rows = []
        tenant = SimpleNamespace(pk=71, code="qa-ymath-realuse-fe-123-1-abcdef123456")
        def model(**kwargs):
            return SimpleNamespace(**kwargs, _meta=SimpleNamespace(concrete_fields=[
                SimpleNamespace(attname=key) for key in kwargs if key not in {"user", "parent"}
            ]), refresh_from_db=lambda: None)
        student_user = model(pk=17, tenant_id=71, password="student-hash", account_notice_password_ciphertext="cipher")
        parent_user = model(pk=19, tenant_id=71, password="original-parent-hash", account_notice_password_ciphertext="cipher")
        parent = model(pk=11, tenant_id=71, user_id=19, user=parent_user)
        student = model(pk=23, tenant_id=71, user_id=17, parent_id=11, user=student_user, parent=parent,
                        name=probe.fixture_prefix(tenant.code) + "test")
        class Query:
            def values_list(self, *args, **kwargs):
                return list(rows)
            def exists(self):
                return bool(rows)
            def count(self):
                return len(rows)
            def delete(self):
                rows.clear()
        audits = SimpleNamespace(filter=lambda **kwargs: Query(), create=lambda **kwargs: rows.append(kwargs["payload"]))
        modules = {
            "django.conf": SimpleNamespace(settings=SimpleNamespace(SECRET_KEY="synthetic-key")),
            "django.contrib.auth.hashers": SimpleNamespace(check_password=lambda *args: True),
            "apps.core.models": SimpleNamespace(OpsAuditLog=SimpleNamespace(objects=audits),
                TenantMembership=SimpleNamespace(objects=SimpleNamespace(filter=lambda **kwargs: SimpleNamespace(exists=lambda: True)))),
            "apps.core.services.account_credentials": SimpleNamespace(recover_password=lambda *args, **kwargs: "synthetic"),
            "apps.core.models.user": SimpleNamespace(user_display_username=lambda user: "synthetic"),
            "apps.domains.students.models": SimpleNamespace(Student=SimpleNamespace(objects=SimpleNamespace(
                filter=lambda **kwargs: SimpleNamespace(select_related=lambda *args: SimpleNamespace(first=lambda: student))))),
        }
        with patch.dict(sys.modules, modules), patch.object(probe, "runtime_scope", return_value="a" * 64), \
                patch.object(probe, "authenticate", return_value=True):
            snapshot = probe.run_probe(tenant, 23, "snapshot", "fixed")
            self.assertEqual(snapshot["snapshot_count"], 1)
            parent_user.password = "changed-parent-hash"
            with self.assertRaisesRegex(probe.ProbeFailure, "parent-hash-changed"):
                probe.run_probe(tenant, 23, "compare", "fixed")
            self.assertEqual(len(rows), 1, "failure must retain the owned baseline until fixed cleanup")
            parent_user.password = "original-parent-hash"
            compared = probe.run_probe(tenant, 23, "compare", "fixed")
            self.assertTrue(compared["parent_hash_preserved"])
            self.assertEqual(compared["snapshot_count"], 0)
            self.assertNotIn("original-parent-hash", json.dumps(compared))
            rows.append({"scope_sha256": "b" * 64, "seal": "private"})
            with self.assertRaisesRegex(probe.ProbeFailure, "snapshot-owner-mismatch"):
                probe.cleanup_snapshots(tenant)
            self.assertEqual(len(rows), 1)
            rows[0]["scope_sha256"] = "a" * 64
            probe.cleanup_snapshots(tenant)
            self.assertEqual(rows, [])

    def test_runtime_rejects_production_before_querying_owner_or_authenticating(self):
        modules = {
            "django.conf": SimpleNamespace(settings=SimpleNamespace(DATABASES={"default": {"NAME": "production", "USER": "production"}})),
            "apps.core.models": SimpleNamespace(OpsAuditLog=None),
            "apps.core.management.commands.setup_ymath_realuse_scenario": SimpleNamespace(assert_isolated_runtime=lambda: self.fail("must reject first")),
        }
        with patch.dict(sys.modules, modules), patch.dict(probe.os.environ, {}, clear=True):
            with self.assertRaisesRegex(probe.ProbeFailure, "runtime-scope-invalid"):
                probe.runtime_scope(SimpleNamespace(pk=10, code="movementhui"))


if __name__ == "__main__":
    unittest.main()
