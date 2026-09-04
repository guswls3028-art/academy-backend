"""Development host parameter boundary: read-only plan by default.

Audit all grants and simulate the proposed explicit deny without retrieving any
parameter value. The separate frontend QA role must not inherit this host role.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid


ROOT = Path(__file__).resolve().parents[2]
ROLE = "academy-api-development-role"
INLINE = "academy-api-development-runtime"
ACCOUNT = "809466760795"
REGION = "ap-northeast-2"
BOUNDARY = ROOT / "scripts/v1/templates/iam/policy_api_development_parameter_boundary.json"
LOCK_TABLE = "academy-v1-video-job-lock"
LOCK_TTL = 10800
LOCK_HELPER = Path(__file__).with_name("deployment_lock.py")


def aws_environment(profile):
    if not profile or profile != profile.strip():
        raise ValueError("An exact named AWS profile is required")
    environment = os.environ.copy()
    # Both IAM (--profile) and the unchanged lock helper (ambient profile) must
    # use the selected profile, never a different inherited credential tuple.
    for key in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "AWS_SECURITY_TOKEN",
                "AWS_DEFAULT_PROFILE", "AWS_ROLE_ARN", "AWS_WEB_IDENTITY_TOKEN_FILE"):
        environment.pop(key, None)
    environment.update(AWS_PROFILE=profile, AWS_REGION=REGION, AWS_DEFAULT_REGION=REGION,
                       AWS_MAX_ATTEMPTS="1", AWS_EC2_METADATA_DISABLED="true")
    return environment


def aws(args, profile):
    command = ["aws", *args, "--region", REGION, "--output", "json"]
    if profile:
        command.extend(["--profile", profile])
    command.extend(["--cli-connect-timeout", "5", "--cli-read-timeout", "15"])
    result = subprocess.run(command, text=True, capture_output=True, check=False,
                            timeout=30, env=aws_environment(profile))
    if result.returncode:
        # Never echo an entire AWS response or command containing future inputs.
        code = next((code for code in ("NoSuchEntity", "InvalidDocument") if code in result.stderr), "failed")
        raise RuntimeError(f"AWS operation {' '.join(args[:2])}: {code}")
    return json.loads(result.stdout or "{}")


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def policy_hash(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def lock_action(action, owner, profile):
    result = subprocess.run(
        [sys.executable, str(LOCK_HELPER), action, "--owner", owner,
         "--table-name", LOCK_TABLE, "--ttl-seconds", str(LOCK_TTL)],
        text=True, capture_output=True, check=False, timeout=30, env=aws_environment(profile),
    )
    if result.returncode:
        raise RuntimeError(f"Shared deployment lock {action} failed")


def host_snapshot(profile, checkpoint=lambda: None):
    """Non-secret inventory; exclude the target policy from the stable fingerprint."""
    def read(args):
        checkpoint()
        result = aws(args, profile)
        checkpoint()
        return result

    if read(["sts", "get-caller-identity"])["Account"] != ACCOUNT:
        raise ValueError("Wrong AWS account")
    role = read(["iam", "get-role", "--role-name", ROLE])["Role"]
    role_arn = f"arn:aws:iam::{ACCOUNT}:role/{ROLE}"
    trust = json.loads((BOUNDARY.parent / "trust_ec2.json").read_text(encoding="utf-8"))
    if (role.get("Arn") != role_arn or not role.get("RoleId") or
            role.get("AssumeRolePolicyDocument") != trust or role.get("PermissionsBoundary")):
        raise ValueError("Host role identity/trust/boundary differs from the exact EC2 contract")
    names = read(["iam", "list-role-policies", "--role-name", ROLE])["PolicyNames"]
    if names != [INLINE]:
        raise ValueError("Unexpected host inline policy inventory")
    document = read(["iam", "get-role-policy", "--role-name", ROLE, "--policy-name", INLINE])["PolicyDocument"]
    attached = read(["iam", "list-attached-role-policies", "--role-name", ROLE])["AttachedPolicies"]
    core_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
    if len(attached) != 1 or attached[0]["PolicyArn"] != core_arn:
        raise ValueError("Unexpected host managed policy inventory")
    metadata = read(["iam", "get-policy", "--policy-arn", core_arn])["Policy"]
    version = read(["iam", "get-policy-version", "--policy-arn", core_arn,
                    "--version-id", metadata["DefaultVersionId"]])["PolicyVersion"]
    if version["VersionId"] != metadata["DefaultVersionId"]:
        raise ValueError("Managed policy version changed during inventory")
    profiles = read(["iam", "list-instance-profiles-for-role", "--role-name", ROLE])["InstanceProfiles"]
    profile_arn = f"arn:aws:iam::{ACCOUNT}:instance-profile/academy-api-development"
    if len(profiles) != 1:
        raise ValueError("Host role must have exactly one profile")
    instance_profile = profiles[0]
    roles = instance_profile["Roles"]
    if (instance_profile.get("Arn") != profile_arn or not instance_profile.get("InstanceProfileId") or
            instance_profile.get("InstanceProfileName") != "academy-api-development" or len(roles) != 1 or
            roles[0].get("Arn") != role_arn or roles[0].get("RoleId") != role["RoleId"]):
        raise ValueError("Host profile identity/binding changed")
    reservations = read(["ec2", "describe-instances", "--filters", f"Name=iam-instance-profile.arn,Values={profile_arn}",
                         "Name=instance-state-name,Values=pending,running,stopping,stopped,shutting-down"])["Reservations"]
    instances = [instance for reservation in reservations for instance in reservation["Instances"]]
    if len(instances) != 1:
        raise ValueError("Expected exactly one non-terminated development host")
    instance = instances[0]
    tags = {tag["Key"]: tag["Value"] for tag in instance.get("Tags", [])}
    expected_tags = {"Name": "academy-v1-api-development", "Environment": "development",
                     "ManagedBy": "academy-api-development", "Lifecycle": "active"}
    if (instance["State"]["Name"] != "running" or instance.get("IamInstanceProfile", {}).get("Arn") != profile_arn or
            any(tags.get(key) != value for key, value in expected_tags.items())):
        raise ValueError("Development host is not the unique running active target")
    protection = read(["ec2", "describe-instance-attribute", "--instance-id", instance["InstanceId"],
                       "--attribute", "disableApiTermination"])["DisableApiTermination"]["Value"]
    if protection is not True:
        raise ValueError("Development host must remain termination protected")
    inventory = {
        "role_arn": role_arn, "role_id": role["RoleId"], "trust": trust, "permissions_boundary": None,
        "inline_names": names, "managed_grants": [{"kind": "managed", "name": attached[0]["PolicyName"],
            "arn": core_arn, "version": version["VersionId"], "document": version["Document"]}],
        "profile_arn": profile_arn, "profile_id": instance_profile["InstanceProfileId"],
        "instance_id": instance["InstanceId"], "instance_state": "running", "tags": expected_tags,
        "termination_protected": True,
    }
    return {"policy": document, "inventory": inventory}


def append_boundary(document):
    statements = proposed_boundary()["Statement"]
    sids = {"DenyParameterReadsOutsideDevelopment", "DenyParameterEnumerationAndHistory"}
    if (len(statements) != 2 or {item["Sid"] for item in statements} != sids or
            any(item["Effect"] != "Deny" for item in statements)):
        raise ValueError("Expected exactly the reviewed two Deny statements")
    if any(statement.get("Sid") in sids for statement in document["Statement"]):
        raise ValueError("Boundary Sids already exist; use read-only verification or replan")
    return {**document, "Statement": document["Statement"] + statements}


def require_hashes(*values):
    if any(not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value) for value in values):
        raise ValueError("Reviewed canonical SHA256 values are required")


def verify_host_boundary(profile, expected_policy_hash, expected_inventory_hash, checkpoint=lambda: None):
    require_hashes(expected_policy_hash, expected_inventory_hash)
    snapshot = host_snapshot(profile, checkpoint)
    if policy_hash(snapshot["policy"]) != expected_policy_hash or policy_hash(snapshot["inventory"]) != expected_inventory_hash:
        raise ValueError("Current host policy/inventory hash mismatch")
    statements = snapshot["policy"]["Statement"]
    for expected in proposed_boundary()["Statement"]:
        matches = [item for item in statements if item.get("Sid") == expected["Sid"]]
        if matches != [expected]:
            raise ValueError("Current policy lacks the exact unique deny boundary")
    cases = simulation_cases()
    actual = []
    for action, wildcard in dict.fromkeys((case[0], case[1] == "*") for case in cases):
        group = [case for case in cases if case[0] == action and (case[1] == "*") == wildcard]
        expectations = {resource: decision for _, resource, decision in group}
        checkpoint()
        response = aws(["iam", "simulate-principal-policy", "--policy-source-arn", f"arn:aws:iam::{ACCOUNT}:role/{ROLE}",
                        "--action-names", action, "--resource-arns", *expectations], profile)
        checkpoint()
        seen = set()
        for result in response["EvaluationResults"]:
            if result.get("EvalActionName") != action:
                raise ValueError("Unexpected simulation action")
            items = result.get("ResourceSpecificResults") or [{
                "EvalResourceName": result["EvalResourceName"], "EvalResourceDecision": result["EvalDecision"]}]
            for item in items:
                resource = "*" if list(expectations) == ["*"] else item["EvalResourceName"]
                decision = item["EvalResourceDecision"]
                if (resource not in expectations or resource in seen or decision != expectations[resource] or
                        result.get("MissingContextValues") or item.get("MissingContextValues")):
                    raise ValueError("Current-principal simulation is missing, duplicate, or mismatched")
                seen.add(resource)
                actual.append({"action": action, "resource": resource, "decision": decision})
        if seen != set(expectations):
            raise ValueError("Current-principal simulation omitted a target")
    final = host_snapshot(profile, checkpoint)
    if policy_hash(final["policy"]) != expected_policy_hash or policy_hash(final["inventory"]) != expected_inventory_hash:
        raise ValueError("Host policy/inventory changed during simulation")
    return {"mode": "HOST_POLICY_VERIFIED", "policy_sha256": expected_policy_hash,
            "inventory_sha256": expected_inventory_hash, "cases": actual, "iam_mutation": 0,
            "limitation": "IAM simulation only; no KMS decryption, live session or real QA proof"}


class HostApplyError(RuntimeError):
    def __init__(self, report):
        self.report = report
        super().__init__(report["mode"])


def apply_host_boundary(profile, expected_current_hash, expected_proposed_hash, expected_inventory_hash):
    require_hashes(expected_current_hash, expected_proposed_hash, expected_inventory_hash)
    aws_environment(profile)
    if os.environ.get("ACADEMY_DEPLOY_LOCK_OWNER") or os.environ.get("ACADEMY_RUNTIME_ENV_LOCK_OWNER"):
        raise ValueError("Host apply is standalone-only; inherited lease owners are forbidden")
    for key, expected in (("ACADEMY_DEPLOY_LOCK_TABLE", LOCK_TABLE), ("AWS_REGION", REGION), ("AWS_DEFAULT_REGION", REGION)):
        if os.environ.get(key) and os.environ[key] != expected:
            raise ValueError("Host apply lock table/region override is forbidden")
    # Verify the account before touching DDB; all subprocesses share one exact
    # profile-only environment. Neither a profile name nor a plan authorizes Apply.
    if aws(["sts", "get-caller-identity"], profile)["Account"] != ACCOUNT:
        raise ValueError("Wrong AWS account")
    owner = f"host-parameter-boundary:{os.getpid()}:{uuid.uuid4().hex}"
    deadline = time.monotonic() + 600
    report = {"mode": "HOST_POLICY_FAILED", "role": ROLE, "inline_policy": INLINE, "owner": owner,
              "iam_write_attempted": False, "lock_state": "not_acquired", "verification": None, "stage": "acquire",
              "lease_expiry_estimate_unix": int(time.time()) + LOCK_TTL}
    acquired = False
    verified = False
    failure = None

    def checkpoint():
        if time.monotonic() >= deadline:
            raise TimeoutError("Host operation deadline exceeded")
        lock_action("assert-owned", owner, profile)
        if time.monotonic() >= deadline:
            raise TimeoutError("Host operation deadline exceeded")

    try:
        report["lock_state"] = "acquisition_unconfirmed"
        lock_action("acquire", owner, profile)
        acquired = True
        report["lock_state"] = "retained_until_ttl"
        report["lease_expiry_estimate_unix"] = int(time.time()) + LOCK_TTL
        report["stage"] = "inventory"
        snapshot = host_snapshot(profile, checkpoint)
        if policy_hash(snapshot["policy"]) != expected_current_hash or policy_hash(snapshot["inventory"]) != expected_inventory_hash:
            raise ValueError("Reviewed current policy/inventory changed")
        proposed = append_boundary(snapshot["policy"])
        if policy_hash(proposed) != expected_proposed_hash:
            raise ValueError("Reviewed proposed policy changed")
        report["stage"] = "prewrite"
        checkpoint()
        lock_action("renew", owner, profile)
        report["lease_expiry_estimate_unix"] = int(time.time()) + LOCK_TTL
        current = aws(["iam", "get-role-policy", "--role-name", ROLE, "--policy-name", INLINE], profile)["PolicyDocument"]
        if policy_hash(current) != expected_current_hash:
            raise ValueError("Current policy changed immediately before dispatch")
        checkpoint()
        # A transport failure can occur after IAM commits. Set before dispatch;
        # never retry the put, restore old permissions, or release blindly.
        report["iam_write_attempted"] = True
        report["stage"] = "put"
        aws(["iam", "put-role-policy", "--role-name", ROLE, "--policy-name", INLINE,
             "--policy-document", canonical(proposed)], profile)
        report["stage"] = "postverify"
        report["verification"] = verify_host_boundary(profile, expected_proposed_hash, expected_inventory_hash, checkpoint)
        checkpoint()
        verified = True
    except BaseException as error:
        failure = type(error).__name__
    finally:
        if acquired:
            try:
                lock_action("assert-owned", owner, profile)
            except BaseException as error:
                failure = type(error).__name__
                report["lock_state"] = "ownership_unconfirmed"
            else:
                if report["iam_write_attempted"] and not verified:
                    report["lock_state"] = "retained_until_ttl"
                else:
                    try:
                        lock_action("release", owner, profile)
                        report["lock_state"] = "released"
                    except BaseException as error:
                        failure = type(error).__name__
                        report["lock_state"] = "release_failed"
    if failure:
        report["mode"] = "HOST_POLICY_INDETERMINATE" if report["iam_write_attempted"] else "HOST_POLICY_FAILED"
        report["error_type"] = failure
        raise HostApplyError(report)
    report["mode"] = "HOST_POLICY_APPLIED"
    report["stage"] = "complete"
    return report


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
    snapshot = host_snapshot(profile)
    inline_document = snapshot["policy"]
    inventory = snapshot["inventory"]
    grants = [{"kind": "inline", "name": INLINE, "document": inline_document}, *inventory["managed_grants"]]
    boundary = proposed_boundary()
    after = append_boundary(inline_document)
    evidence = {
        "mode": "READ_ONLY_PLAN", "role": ROLE, "inline_policy": INLINE,
        "trust": inventory["trust"], "permissions_boundary": None,
        "inventory": inventory, "inventory_sha256": policy_hash(inventory),
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
            "ssm:resourceTag/Environment": "development", "ssm:resourceTag/Lifecycle": "active"}
    user = "UNITROLE:academy-fe-qa-123-1"
    owned = {"aws:userid": user, "ssm:resourceTag/aws:ssmmessages:session-id": user}
    document_check = "ssm:SessionDocumentAccessCheck"
    # Synthetic controls for unrelated policy keys avoid unexplained simulator
    # omissions. These are not observed AWS request/Session.Owner values.
    without_document_check = {**tags, **owned}
    positive = {**without_document_check, document_check: "true"}
    cases = [
        ("ssm:StartSession", instance, without_document_check, "allowed"),
        ("ssm:StartSession", instance, {**positive, "ssm:resourceTag/Name": "foreign"}, "implicitDeny"),
        ("ssm:StartSession", instance, {**positive, "ssm:resourceTag/ManagedBy": "foreign"}, "implicitDeny"),
        ("ssm:StartSession", instance, {**positive, "ssm:resourceTag/Environment": "production"}, "implicitDeny"),
        ("ssm:StartSession", instance, {**positive, "ssm:resourceTag/Lifecycle": "candidate"}, "implicitDeny"),
        ("ssm:GetParameter", prefix + "parameter/academy/api/development/ymath-realuse-password", positive, "allowed"),
        ("ssm:GetParameter", prefix + "parameter/academy/api/env", positive, "implicitDeny"),
        ("ssm:GetParameter", prefix + "parameter/academy/api/development/env", positive, "implicitDeny"),
        ("ssm:GetParametersByPath", prefix + "parameter/academy", positive, "implicitDeny"),
        ("ssm:GetCommandInvocation", "*", positive, "explicitDeny"),
        ("ssm:ListCommandInvocations", "*", positive, "explicitDeny"),
        ("ssm:SendCommand", "*", positive, "explicitDeny"),
        ("ssm:TerminateSession", prefix + "session/academy-fe-qa-123-1-unit", positive, "allowed"),
        ("ssm:TerminateSession", prefix + "session/academy-fe-qa-123-1-unit",
         {**positive, "ssm:resourceTag/aws:ssmmessages:session-id": "FOREIGN:other"}, "implicitDeny"),
        # AWS does not support resource-level ARN conditions for this action.
        # The StartSession session/caller-bound TokenValue authorizes the stream;
        # this simulation cannot prove a foreign-channel denial.
        ("ssmmessages:OpenDataChannel", "*", positive, "allowed"),
        ("iam:GetRolePolicy", f"arn:aws:iam::{ACCOUNT}:role/academy-api-development-role", positive, "allowed"),
        ("iam:GetRolePolicy", f"arn:aws:iam::{ACCOUNT}:role/academy-gha-ecr-build", positive, "implicitDeny"),
        ("ec2:TerminateInstances", instance, positive, "implicitDeny"),
    ]
    approved = [prefix + "document/" + name for name in (role_name, "academy-frontend-development-api-port")]
    # Amazon-owned public documents have an empty account component. The two
    # default-document forms and unapproved name are negative ARN controls,
    # not evidence of the service's omitted-DocumentName resolution.
    denied = [prefix + "document/SSM-SessionManagerRunShell",
              f"arn:aws:ssm:{REGION}::document/SSM-SessionManagerRunShell",
              prefix + "document/unapproved-session-negative-only"]
    denied += [f"arn:aws:ssm:{REGION}::document/{name}" for name in (
        "AWS-StartInteractiveCommand", "AWS-StartSSHSession", "AWS-StartPortForwardingSession",
        "AWS-StartPortForwardingSessionToRemoteHost")]
    for resource in approved + denied:
        for value in (None, "false", "true"):
            context = dict(without_document_check)
            if value is not None:
                context[document_check] = value
            expected = "allowed" if resource in approved and value == "true" else "implicitDeny"
            cases.append(("ssm:StartSession", resource, context, expected))
    evidence = {"mode": "READ_ONLY_FRONTEND_ROLE_PLAN", "role": role_name,
                "documents": documents, "canonical_sha256": {
                    name: hashlib.sha256(canonical(document).encode()).hexdigest() for name, document in documents.items()},
                "cases": [], "iam_mutation": 0, "ssm_document_mutation": 0, "parameter_value_reads": 0,
                "limitation": "Identity-policy simulation only; no OIDC exchange, SSM execution, KMS decryption or real QA proof"}
    for action, resource, context, expected in cases:
        # Use the explicit case context verbatim: never refill an intentionally
        # missing document key from a common positive/default context.
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
        and set(item["missing_context"]) <= {document_check} - item["context"].keys()
        for item in evidence["cases"])
    return evidence


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--aws-profile", default="default")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--frontend-role-plan", action="store_true", help="Separate frontend role/document plan; never Apply")
    mode.add_argument("--apply-host-boundary", action="store_true", help="Explicit separately authorized host-only mutation")
    mode.add_argument("--verify-host-boundary", action="store_true", help="Current principal verification; never writes")
    parser.add_argument("--expected-current-hash")
    parser.add_argument("--expected-proposed-hash")
    parser.add_argument("--expected-inventory-hash")
    arguments = parser.parse_args(argv)
    hashes = (arguments.expected_current_hash, arguments.expected_proposed_hash, arguments.expected_inventory_hash)
    if arguments.apply_host_boundary:
        if not all(hashes):
            parser.error("host Apply requires all three reviewed hashes")
    elif arguments.verify_host_boundary:
        if hashes[0] or not all(hashes[1:]):
            parser.error("host verification requires only proposed and inventory hashes")
    elif any(hashes):
        parser.error("expected hashes require an explicit host Apply or verification mode")
    try:
        if arguments.apply_host_boundary:
            report = apply_host_boundary(arguments.aws_profile, *hashes)
        elif arguments.verify_host_boundary:
            report = verify_host_boundary(arguments.aws_profile, *hashes[1:])
        else:
            report = frontend_plan(arguments.aws_profile) if arguments.frontend_role_plan else audit(arguments.aws_profile)
        print(json.dumps(report, indent=2))
        return 0 if report.get("proposed_simulation_pass", True) else 1
    except HostApplyError as error:
        print(json.dumps(error.report, indent=2))
        return 1
    except (RuntimeError, ValueError, KeyError, subprocess.SubprocessError) as error:
        print(json.dumps({"mode": "FAILED", "error_type": type(error).__name__}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
