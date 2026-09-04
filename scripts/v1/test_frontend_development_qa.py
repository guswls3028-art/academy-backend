"""Offline contracts; never load credentials or contact AWS."""
import json
import re
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
BOUNDARY = ROOT / "scripts/v1/templates/iam/policy_api_development_parameter_boundary.json"


class DevelopmentParameterBoundaryTests(unittest.TestCase):
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
        self.assertEqual(set(session["parameters"]), {"Action", "TenantCode", "ReleaseId", "ApiDigest"})
        for parameter in session["parameters"].values():
            for unsafe in ("'; touch /tmp/escape; '", "$(id)", "{{ssm:/academy/api/env}}", "x\ny", "../production"):
                self.assertIsNone(re.fullmatch(parameter["allowedPattern"], unsafe))
        command = session["properties"]["linux"]["commands"]
        self.assertEqual(set(re.findall(r"{{\s*([^}]+?)\s*}}", command)), set(session["parameters"]))
        python = command.split("<<'ACADEMY_QA_PY'\n", 1)[1].rsplit("ACADEMY_QA_PY", 1)[0]
        compile(python, "fixed-development-session", "exec")
        self.assertIn('command._exact_tenant_or_fail_on_case_variant(tenant) is None', python)
        self.assertIn('payload["remaining"] == {"tenants": 0, "users": 0}', python)
        self.assertNotIn("reset=True", python)
        port = json.loads((directory / "frontend_development_api_port.json").read_text())
        self.assertEqual(port["properties"], {"portNumber": "8000", "localPortNumber": "18000", "type": "LocalPortForwarding"})
        self.assertNotIn("parameters", port)


if __name__ == "__main__":
    unittest.main()
