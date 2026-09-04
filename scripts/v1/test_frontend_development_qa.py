"""Offline contracts; never load credentials or contact AWS."""
import ast
import hashlib
import hmac
import io
import json
import os
import re
import sys
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, MagicMock, patch


ROOT = Path(__file__).resolve().parents[2]
BOUNDARY = ROOT / "scripts/v1/templates/iam/policy_api_development_parameter_boundary.json"


class DevelopmentParameterBoundaryTests(unittest.TestCase):
    def test_cleanup_binds_creation_capability_and_refuses_other_runs_offline(self):
        session = json.loads((ROOT / "scripts/v1/templates/ssm/frontend_development_qa.json").read_text())
        source = session["properties"]["linux"]["commands"].split("<<'ACADEMY_QA_PY'\n", 1)[1].rsplit("ACADEMY_QA_PY", 1)[0]
        functions = [node for node in ast.parse(source).body if isinstance(node, ast.FunctionDef)
                     and node.name in {"ownership_payload", "assert_cleanup_owner"}]
        self.assertEqual(len(functions), 2, "missing server-side creation/cleanup ownership binding")
        namespace = {"hashlib": hashlib, "hmac": hmac, "re": re}
        exec(compile(ast.Module(body=functions, type_ignores=[]), "fixed-ownership", "exec"), namespace)
        payload = namespace["ownership_payload"]
        check = namespace["assert_cleanup_owner"]
        first = "qa-ymath-realuse-fe-123-1-abcdef123456"
        other = "qa-ymath-realuse-fe-456-1-fedcba654321"
        owner = "a" * 64
        foreign = "b" * 64
        record = payload(first, 71, owner)
        destroyed = []

        def cleanup(code, tenant_id, capability, records):
            check(code, tenant_id, capability, records)
            destroyed.append(code)  # Local spy only; no Django, DB or AWS calls.

        for request in [(first, 71, foreign, [record]), (other, 72, owner, [record]),
                        (first, 72, owner, [record]), (first, 71, owner, []),
                        (first, 71, owner, [record, record]), (first, 71, owner, [{}])]:
            with self.assertRaises(PermissionError):
                cleanup(*request)
        self.assertEqual(destroyed, [])
        cleanup(first, 71, owner, [record])
        self.assertEqual(destroyed, [first])
        self.assertNotIn(owner, json.dumps(record))
        self.assertIn('OpsAuditLog.objects.create(', source)
        self.assertIn('assert_cleanup_owner(tenant, existing.pk, capability, records)', source)
        self.assertLess(source.index('assert_cleanup_owner(tenant, existing.pk, capability, records)'),
                        source.index('destroy=True'))

    def test_fixed_documents_bound_remote_session_lifetime_and_operation(self):
        directory = ROOT / "scripts/v1/templates/ssm"
        for filename, duration in [("frontend_development_qa.json", "5"),
                                   ("frontend_development_api_port.json", "25")]:
            document = json.loads((directory / filename).read_text())
            self.assertEqual(document.get("inputs"), {"maxSessionDuration": duration, "idleSessionTimeout": "5"})
        command = json.loads((directory / "frontend_development_qa.json").read_text())["properties"]["linux"]["commands"]
        self.assertIn("signal.alarm(180)", command)
        self.assertIn("timeout --kill-after=5s 210s docker exec", command)

    def test_actual_fixed_cleanup_control_flow_never_destroys_foreign_or_unowned_tenant(self):
        session = json.loads((ROOT / "scripts/v1/templates/ssm/frontend_development_qa.json").read_text())
        source = session["properties"]["linux"]["commands"].split("<<'ACADEMY_QA_PY'\n", 1)[1].rsplit("ACADEMY_QA_PY", 1)[0]
        functions = [node for node in ast.parse(source).body if isinstance(node, ast.FunctionDef)]
        namespace = {"hashlib": hashlib, "hmac": hmac, "re": re, "io": io, "json": json,
                     "os": os, "django": SimpleNamespace(setup=Mock())}
        exec(compile(ast.Module(body=functions, type_ignores=[]), "fixed-actual-cleanup", "exec"), namespace)
        tenant = "qa-ymath-realuse-fe-456-1-fedcba654321"
        capability = "a" * 64
        foreign_owner = namespace["ownership_payload"](tenant, 72, "b" * 64)
        own_record = namespace["ownership_payload"](tenant, 72, capability)
        command = Mock()
        command._exact_tenant_or_fail_on_case_variant.return_value = SimpleNamespace(pk=72)
        command._remaining_for_code.return_value = {"tenants": 1, "users": 2}
        cursor = MagicMock()
        audit = Mock()
        destroy = Mock(side_effect=lambda *args, **kwargs: kwargs["stdout"].write(json.dumps({
            "status": "YMATH_REALUSE_SCENARIO_DESTROYED", "tenant_code": tenant,
            "remaining": {"tenants": 0, "users": 0},
        })))
        bucket_keys = ("R2_AI_BUCKET", "R2_STORAGE_BUCKET", "R2_ADMIN_BUCKET", "R2_VIDEO_BUCKET", "R2_EXCEL_BUCKET")
        settings = SimpleNamespace(
            VIDEO_BATCH_JOB_QUEUE="", VIDEO_BATCH_JOB_DEFINITION="",
            TOOLS_SQS_QUEUE_NAME="academy-v1-development-tools-queue",
            MESSAGING_SQS_QUEUE_NAME="academy-v1-development-messaging-queue",
            DATABASES={"default": {"NAME": "academy_api_development", "USER": "academy_api_development_app"}},
            **{key: "academy-development-artifacts" for key in bucket_keys})
        modules = {
            "django.conf": SimpleNamespace(settings=settings),
            "django.core.management": SimpleNamespace(call_command=destroy),
            "django.db": SimpleNamespace(transaction=SimpleNamespace(atomic=nullcontext),
                                         connection=SimpleNamespace(cursor=lambda: cursor)),
            "apps.core.models": SimpleNamespace(OpsAuditLog=SimpleNamespace(objects=audit)),
            "apps.core.management.commands.setup_ymath_realuse_scenario":
                SimpleNamespace(Command=lambda: command, assert_isolated_runtime=Mock()),
        }
        digest = "sha256:" + "b" * 64
        release = "sha-" + "a" * 40 + "-run-123-1"
        env = {"QA_ACTION": "Cleanup", "QA_TENANT": tenant, "QA_CAPABILITY": capability,
               "QA_RELEASE": release, "QA_DIGEST": digest,
               "QA_IMAGE": "809466760795.dkr.ecr.ap-northeast-2.amazonaws.com/academy-api@" + digest,
               "DJANGO_SETTINGS_MODULE": "apps.api.config.settings.development", "ACADEMY_RUNTIME_ENV": "development",
               "ACADEMY_DEVELOPMENT_RELEASE_ID": release, "SOLAPI_MOCK": "true", "TOSS_AUTO_BILLING_ENABLED": "false",
               **{key: "academy-v1-development-ai-queue" for key in
                  ("AI_SQS_QUEUE_NAME_LITE", "AI_SQS_QUEUE_NAME_BASIC", "AI_SQS_QUEUE_NAME_PREMIUM")}}
        with patch.dict(sys.modules, modules), patch.dict(os.environ, env, clear=True):
            for records in ([foreign_owner], [], [own_record, own_record]):
                audit.filter.return_value.values_list.return_value = records
                with self.assertRaises(PermissionError):
                    namespace["run"]()
                destroy.assert_not_called()
                audit.create.assert_not_called()
            audit.filter.return_value.values_list.return_value = [own_record]
            result = namespace["run"]()
            self.assertEqual(result["remaining"], {"tenants": 0, "users": 0})
            self.assertEqual(destroy.call_count, 1)
            self.assertTrue(destroy.call_args.kwargs["destroy"])
            self.assertEqual(destroy.call_args.kwargs["tenant_code"], tenant)
            command._exact_tenant_or_fail_on_case_variant.return_value = None
            command._remaining_for_code.return_value = {"tenants": 0, "users": 0}
            self.assertEqual(namespace["run"]()["status"], "YMATH_REALUSE_SCENARIO_ABSENT")
            self.assertEqual(destroy.call_count, 1, "absent cleanup must not call destroy")

    def test_managed_ssm_parameter_allow_is_explicitly_bounded(self):
        source = (ROOT / "scripts/v1/resources/iam.ps1").read_text(encoding="utf-8-sig")
        development = source.split("function Ensure-ApiDevelopmentIAM {", 1)[1].split(
            "function Legacy-GitHubActionsDeployIAM", 1
        )[0]
        self.assertIn("policy_api_development_parameter_boundary.json", development)
        self.assertIn("$runtimePolicy.Statement +=", development)

    def test_parameter_boundary_denies_recursion_history_and_unlisted_resources(self):
        self.assertTrue(BOUNDARY.exists(), "missing explicit development parameter deny")
        policy = json.loads(BOUNDARY.read_text(encoding="utf-8"))
        by_sid = {statement["Sid"]: statement for statement in policy["Statement"]}
        self.assertEqual(set(by_sid), {"DenyParameterReadsOutsideDevelopment", "DenyParameterEnumerationAndHistory"})
        outside = by_sid["DenyParameterReadsOutsideDevelopment"]
        self.assertEqual(outside["Effect"], "Deny")
        self.assertEqual(set(outside["Action"]), {"ssm:GetParameter", "ssm:GetParameters"})
        self.assertEqual(len(outside["NotResource"]), 5)
        for resource in outside["NotResource"]:
            self.assertNotIn("*", resource)
            self.assertIn("/development/", resource)
        enumeration = by_sid["DenyParameterEnumerationAndHistory"]
        self.assertEqual(enumeration["Effect"], "Deny")
        self.assertEqual(enumeration["Resource"], "*")
        self.assertEqual(set(enumeration["Action"]), {"ssm:GetParametersByPath", "ssm:GetParameterHistory"})

    def test_frontend_role_is_separate_main_only_and_has_no_global_command_output(self):
        directory = ROOT / "scripts/v1/templates/iam"
        trust = json.loads((directory / "trust_frontend_development_qa.json").read_text())
        statement, = trust["Statement"]
        self.assertEqual(statement["Action"], "sts:AssumeRoleWithWebIdentity")
        self.assertEqual(statement["Condition"]["StringEquals"], {
            "token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
            "token.actions.githubusercontent.com:sub": "repo:guswls3028-art/academy-frontend:ref:refs/heads/main",
        })
        policy = json.loads((directory / "policy_frontend_development_qa.json").read_text())
        allows = [entry for entry in policy["Statement"] if entry["Effect"] == "Allow"]
        actions = {action for entry in allows for action in
                   (entry["Action"] if isinstance(entry["Action"], list) else [entry["Action"]])}
        self.assertNotIn("ssm:GetCommandInvocation", actions)
        self.assertNotIn("ssm:SendCommand", actions)
        self.assertFalse(any(action.startswith(("iam:Put", "iam:Create", "ssm:Put", "ssm:Update", "ec2:Run")) for action in actions))
        self.assertNotIn("academy-gha-ecr-build", json.dumps(policy))
        by_sid = {entry["Sid"]: entry for entry in policy["Statement"]}
        self.assertEqual(by_sid["StartVerifiedDevelopmentOnly"]["Condition"]["Bool"], {"ssm:SessionDocumentAccessCheck": "true"})
        self.assertEqual(by_sid["TerminateOwnedSessionOnly"]["Condition"]["StringEquals"], {
            "ssm:resourceTag/aws:ssmmessages:session-id": "${aws:userid}",
        })

    def test_fixed_session_document_has_no_command_parameter_or_interpolation_escape(self):
        directory = ROOT / "scripts/v1/templates/ssm"
        session = json.loads((directory / "frontend_development_qa.json").read_text())
        self.assertEqual(session["sessionType"], "NonInteractiveCommands")
        self.assertEqual(set(session["parameters"]), {"Action", "TenantCode", "ReleaseId", "ApiDigest", "OwnershipCapability"})
        for parameter in session["parameters"].values():
            for unsafe in ("'; touch /tmp/escape; '", "$(id)", "{{ssm:/academy/api/env}}", "x\ny", "../production"):
                self.assertIsNone(re.fullmatch(parameter["allowedPattern"], unsafe))
        command = session["properties"]["linux"]["commands"]
        self.assertEqual(set(re.findall(r"{{\s*([^}]+?)\s*}}", command)), set(session["parameters"]))
        python = command.split("<<'ACADEMY_QA_PY'\n", 1)[1].rsplit("ACADEMY_QA_PY", 1)[0]
        compile(python, "fixed-development-session", "exec")
        self.assertIn('assert existing is None', python)
        self.assertIn('payload["remaining"] == {"tenants": 0, "users": 0}', python)
        self.assertNotIn("reset=True", python)
        port = json.loads((directory / "frontend_development_api_port.json").read_text())
        self.assertEqual(port["properties"], {"portNumber": "8000", "localPortNumber": "18000", "type": "LocalPortForwarding"})
        self.assertNotIn("parameters", port)


if __name__ == "__main__":
    unittest.main()
