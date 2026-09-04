"""Read-only development parameter-boundary plan. No Apply operation exists yet.

Audit all grants and simulate the proposed explicit deny without retrieving any
parameter value. The separate frontend QA role must not inherit this host role.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[2]
ROLE = "academy-api-development-role"
INLINE = "academy-api-development-runtime"
ACCOUNT = "809466760795"
REGION = "ap-northeast-2"
BOUNDARY = ROOT / "scripts/v1/templates/iam/policy_api_development_parameter_boundary.json"


def aws(args, profile):
    command = ["aws", *args, "--region", REGION, "--output", "json"]
    if profile:
        command.extend(["--profile", profile])
    result = subprocess.run(command, text=True, capture_output=True, check=False)
    if result.returncode:
        # Never echo an entire AWS response or command containing future inputs.
        raise RuntimeError(f"AWS metadata operation failed: {' '.join(args[:2])}: {result.stderr.strip()[:1200]}")
    return json.loads(result.stdout)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def proposed_boundary():
    text = BOUNDARY.read_text(encoding="utf-8")
    return json.loads(text.replace("__REGION__", REGION).replace("__ACCOUNT_ID__", ACCOUNT))


def simulation_cases():
    prefix = f"arn:aws:ssm:{REGION}:{ACCOUNT}:parameter"
    allowed = proposed_boundary()["Statement"][0]["NotResource"]
    denied = [f"{prefix}{name}" for name in (
        "/academy/api/env", "/academy/workers/env", "/academy/r2/preprod/credentials",
        "/academy/api/development/env-extra", "/academy/api/development/env/child",
        "/academy/api/Development/env", "/academy/api/development-other/env",
        "/academy/api/*", "/academy/api/development/*",
    )] + ["*"]
    result = []
    for action in ("ssm:GetParameter", "ssm:GetParameters"):
        result.extend((action, resource, "allowed") for resource in allowed)
        result.extend((action, resource, "explicitDeny") for resource in denied)
    for path in ("/academy", "/academy/api", "/academy/api/development", "/academy/api/development/env"):
        result.append(("ssm:GetParametersByPath", f"{prefix}{path}", "explicitDeny"))
    result.append(("ssm:GetParameterHistory", allowed[0], "explicitDeny"))
    for action in ("ssmmessages:CreateControlChannel", "ssmmessages:CreateDataChannel",
                   "ssmmessages:OpenControlChannel", "ssmmessages:OpenDataChannel",
                   "ssm:UpdateInstanceInformation", "ssm:ListInstanceAssociations",
                   "ssm:GetDocument", "ec2messages:GetMessages", "ec2messages:AcknowledgeMessage"):
        result.append((action, "*", "allowed"))
    return result


def audit(profile):
    identity = aws(["sts", "get-caller-identity"], profile)
    if identity["Account"] != ACCOUNT:
        raise RuntimeError("Wrong AWS account; no plan is valid")
    role = aws(["iam", "get-role", "--role-name", ROLE], profile)["Role"]
    inline_names = aws(["iam", "list-role-policies", "--role-name", ROLE], profile)["PolicyNames"]
    attached = aws(["iam", "list-attached-role-policies", "--role-name", ROLE], profile)["AttachedPolicies"]
    grants = []
    inline_document = None
    for name in sorted(inline_names):
        document = aws(["iam", "get-role-policy", "--role-name", ROLE, "--policy-name", name], profile)["PolicyDocument"]
        grants.append({"kind": "inline", "name": name, "document": document})
        if name == INLINE:
            inline_document = document
    for policy in attached:
        metadata = aws(["iam", "get-policy", "--policy-arn", policy["PolicyArn"]], profile)["Policy"]
        version = aws(["iam", "get-policy-version", "--policy-arn", policy["PolicyArn"],
                       "--version-id", metadata["DefaultVersionId"]], profile)["PolicyVersion"]
        grants.append({"kind": "managed", "name": policy["PolicyName"],
                       "version": version["VersionId"], "document": version["Document"]})
    if inline_document is None:
        raise RuntimeError("Expected development runtime inline policy is missing")
    if role.get("PermissionsBoundary"):
        raise RuntimeError("A permissions boundary exists; review its exact document before extending this plan")
    boundary = proposed_boundary()
    known_sids = {statement["Sid"] for statement in boundary["Statement"]}
    existing_sids = {statement.get("Sid") for statement in inline_document["Statement"]}
    if known_sids & existing_sids:
        raise RuntimeError("Boundary Sids already exist; review current policy instead of appending")
    after = {**inline_document, "Statement": inline_document["Statement"] + boundary["Statement"]}
    evidence = {
        "mode": "READ_ONLY_PLAN", "role": ROLE, "inline_policy": INLINE,
        "trust": role["AssumeRolePolicyDocument"], "permissions_boundary": None,
        "grants": grants, "proposed_deny": boundary,
        "before_sha256": hashlib.sha256(canonical(inline_document).encode()).hexdigest(),
        "after_sha256": hashlib.sha256(canonical(after).encode()).hexdigest(),
        "cases": [], "iam_mutation": 0, "parameter_value_reads": 0,
        "limitation": "IAM simulation only; no KMS decryption, resource-policy, SCP or live access success claim",
    }
    arn = f"arn:aws:iam::{ACCOUNT}:role/{ROLE}"
    # Batch by action while preserving every per-resource result. No SSM API is
    # called: parameter names and parent paths are simulator inputs only.
    cases = simulation_cases()
    # IAM rejects a resource list mixing '*' and specific ARNs.
    for action, wildcard in dict.fromkeys((case[0], case[1] == "*") for case in cases):
        group = [case for case in cases if case[0] == action and (case[1] == "*") == wildcard]
        resources = [case[1] for case in group]
        expectations = {case[1]: case[2] for case in group}
        for phase in ("before", "proposed"):
            arguments = ["iam", "simulate-principal-policy", "--policy-source-arn", arn,
                         "--action-names", action, "--resource-arns", *resources]
            if phase == "proposed":
                arguments += ["--policy-input-list", canonical(boundary)]
            try:
                response = aws(arguments, profile)
            except RuntimeError as error:
                raise RuntimeError(f"Simulation {phase} {action}: {error}") from error
            seen = set()
            for result in response["EvaluationResults"]:
                items = result.get("ResourceSpecificResults") or [{
                    "EvalResourceName": result["EvalResourceName"],
                    "EvalResourceDecision": result["EvalDecision"],
                }]
                for item in items:
                    resource = item["EvalResourceName"]
                    # Resource-less agent actions are reported with a synthetic
                    # ARN by some IAM simulator versions; only one '*' was sent.
                    if resource not in expectations and resources == ["*"]:
                        resource = "*"
                    if resource not in expectations:
                        raise RuntimeError("Simulator returned an unrequested resource")
                    seen.add(resource)
                    decision = item["EvalResourceDecision"]
                    missing = result.get("MissingContextValues", []) + item.get("MissingContextValues", [])
                    evidence["cases"].append({"phase": phase, "action": action, "resource": resource,
                                              "decision": decision, "expected": expectations[resource],
                                              "missing_context": missing})
            if seen != set(resources):
                raise RuntimeError("Simulation did not return every exact target")
    failures = [case for case in evidence["cases"] if case["phase"] == "proposed"
                and (case["decision"] != case["expected"] or case["missing_context"])]
    evidence["proposed_simulation_pass"] = not failures
    return evidence


def frontend_plan(profile):
    """Independent new-role plan; no IAM/SSM writes or parameter-value reads."""
    identity = aws(["sts", "get-caller-identity"], profile)
    if identity["Account"] != ACCOUNT:
        raise RuntimeError("Wrong AWS account")
    role_name = "academy-frontend-development-qa"
    try:
        aws(["iam", "get-role", "--role-name", role_name], profile)
    except RuntimeError as error:
        if "NoSuchEntity" not in str(error):
            raise
    else:
        raise RuntimeError("Frontend QA role already exists; audit it before proposing any update")
    paths = {
        "trust": "scripts/v1/templates/iam/trust_frontend_development_qa.json",
        "policy": "scripts/v1/templates/iam/policy_frontend_development_qa.json",
        "qa_document": "scripts/v1/templates/ssm/frontend_development_qa.json",
        "port_document": "scripts/v1/templates/ssm/frontend_development_api_port.json",
    }
    documents = {name: json.loads((ROOT / path).read_text(encoding="utf-8")) for name, path in paths.items()}
    for name in (role_name, "academy-frontend-development-api-port"):
        try:
            aws(["ssm", "get-document", "--name", name], profile)
        except RuntimeError as error:
            if "InvalidDocument" not in str(error):
                raise
        else:
            raise RuntimeError("Proposed SSM document already exists; review exact content before any update")
    prefix = f"arn:aws:ssm:{REGION}:{ACCOUNT}:"
    instance = f"arn:aws:ec2:{REGION}:{ACCOUNT}:instance/i-0db4c5cddac77fc87"
    tags = {"ssm:resourceTag/Name": "academy-v1-api-development",
            "ssm:resourceTag/ManagedBy": "academy-api-development",
            "ssm:resourceTag/Environment": "development", "ssm:resourceTag/Lifecycle": "active",
            "ssm:SessionDocumentAccessCheck": "true"}
    user = "UNITROLE:academy-fe-qa-123-1"
    owned = {"aws:userid": user, "ssm:resourceTag/aws:ssmmessages:session-id": user}
    cases = [
        ("ssm:StartSession", prefix + "document/academy-frontend-development-qa", {}, "allowed"),
        ("ssm:StartSession", prefix + "document/academy-frontend-development-api-port", {}, "allowed"),
        ("ssm:StartSession", prefix + "document/AWS-StartInteractiveCommand", {}, "implicitDeny"),
        ("ssm:StartSession", instance, tags, "allowed"),
        ("ssm:StartSession", instance, {**tags, "ssm:resourceTag/Environment": "production"}, "implicitDeny"),
        ("ssm:StartSession", instance, {**tags, "ssm:resourceTag/Lifecycle": "candidate"}, "implicitDeny"),
        ("ssm:StartSession", instance, {**tags, "ssm:SessionDocumentAccessCheck": "false"}, "implicitDeny"),
        ("ssm:GetParameter", prefix + "parameter/academy/api/development/ymath-realuse-password", {}, "allowed"),
        ("ssm:GetParameter", prefix + "parameter/academy/api/env", {}, "implicitDeny"),
        ("ssm:GetParameter", prefix + "parameter/academy/api/development/env", {}, "implicitDeny"),
        ("ssm:GetParametersByPath", prefix + "parameter/academy", {}, "implicitDeny"),
        ("ssm:GetCommandInvocation", "*", {}, "explicitDeny"),
        ("ssm:ListCommandInvocations", "*", {}, "explicitDeny"),
        ("ssm:SendCommand", "*", {}, "explicitDeny"),
        ("ssm:TerminateSession", prefix + "session/academy-fe-qa-123-1-unit", owned, "allowed"),
        ("ssm:TerminateSession", prefix + "session/academy-fe-qa-123-1-unit",
         {**owned, "ssm:resourceTag/aws:ssmmessages:session-id": "FOREIGN:other"}, "implicitDeny"),
        # AWS does not support resource-level ARN conditions for this action.
        # The StartSession session/caller-bound TokenValue authorizes the stream;
        # this simulation cannot prove a foreign-channel denial.
        ("ssmmessages:OpenDataChannel", "*", {}, "allowed"),
        ("iam:GetRolePolicy", f"arn:aws:iam::{ACCOUNT}:role/academy-api-development-role", {}, "allowed"),
        ("iam:GetRolePolicy", f"arn:aws:iam::{ACCOUNT}:role/academy-gha-ecr-build", {}, "implicitDeny"),
        ("ec2:TerminateInstances", instance, tags, "implicitDeny"),
    ]
    evidence = {"mode": "READ_ONLY_FRONTEND_ROLE_PLAN", "role": role_name,
                "documents": documents, "canonical_sha256": {
                    name: hashlib.sha256(canonical(document).encode()).hexdigest() for name, document in documents.items()},
                "cases": [], "iam_mutation": 0, "ssm_document_mutation": 0, "parameter_value_reads": 0,
                "limitation": "Identity-policy simulation only; no OIDC exchange, SSM execution, KMS decryption or real QA proof"}
    for action, resource, overrides, expected in cases:
        # The simulator may report unused policy context keys on an implicit
        # deny. Supply a complete positive context and change the exact negative
        # dimension, rather than count a missing context as a security proof.
        context = {**tags, **owned, **overrides}
        arguments = ["iam", "simulate-custom-policy", "--policy-input-list", canonical(documents["policy"]),
                     "--action-names", action, "--resource-arns", resource]
        if context:
            arguments.extend(["--context-entries", json.dumps([
                {"ContextKeyName": key, "ContextKeyValues": [value],
                 "ContextKeyType": "boolean" if key == "ssm:SessionDocumentAccessCheck" else "string"}
                for key, value in context.items()])])
        result, = aws(arguments, profile)["EvaluationResults"]
        evidence["cases"].append({"action": action, "resource": resource, "context": context,
                                  "expected": expected, "decision": result["EvalDecision"],
                                  "missing_context": result.get("MissingContextValues", [])})
    evidence["proposed_simulation_pass"] = all(item["decision"] == item["expected"]
                                                and not item["missing_context"] for item in evidence["cases"])
    return evidence


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--aws-profile", default="default")
    parser.add_argument("--frontend-role-plan", action="store_true", help="Separate new frontend role/document plan; never Apply")
    arguments = parser.parse_args()
    report = frontend_plan(arguments.aws_profile) if arguments.frontend_role_plan else audit(arguments.aws_profile)
    print(json.dumps(report, indent=2))
    raise SystemExit(0 if report["proposed_simulation_pass"] else 1)
