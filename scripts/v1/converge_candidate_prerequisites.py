"""Plan/apply nonsecret candidate IAM and QA image prerequisites.

Does not create, read or transfer secret values. Apply requires clean exact main,
the shared deployment lock and preconfigured main-only GitHub environments.
"""
from __future__ import annotations

import argparse
import hashlib
import urllib.error
import json
import os
import subprocess
from pathlib import Path

from candidate_manifest import IMAGE_NAMES, REPOSITORY, github, require

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = ROOT / "scripts/v1/templates/iam"
ACCOUNT = "809466760795"
REGION = "ap-northeast-2"


def document(name):
    return json.loads((TEMPLATES / name).read_text())


def provision_environment(mode):
    """Create only missing environments; never rewrite existing protection rules."""
    endpoint = f"repos/{REPOSITORY}/environments/candidate-{mode}"
    try:
        github(endpoint)
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            raise
        payload = {"deployment_branch_policy": {"protected_branches": False, "custom_branch_policies": True}}
        subprocess.run(["gh", "api", "--method", "PUT", endpoint, "--input", "-"],
                       input=json.dumps(payload), text=True, capture_output=True, check=True)
        subprocess.run(["gh", "api", "--method", "POST", endpoint + "/deployment-branch-policies", "--input", "-"],
                       input=json.dumps({"name": "main", "type": "branch"}), text=True,
                       capture_output=True, check=True)
    verify_environment(mode)


def verify_environment(mode):
    value = github(f"repos/{REPOSITORY}/environments/candidate-{mode}")
    rule = value.get("deployment_branch_policy") or {}
    require(rule.get("custom_branch_policies") is True and rule.get("protected_branches") is False,
            "Candidate environment requires selected exact-main deployment policy")
    branches = github(f"repos/{REPOSITORY}/environments/candidate-{mode}/deployment-branch-policies")
    policies = branches.get("branch_policies", [])
    require(branches.get("total_count") == 1 and len(policies) == 1
            and policies[0].get("name") == "main" and policies[0].get("type") == "branch",
            "Candidate environment must permit only branch main")


def readback(iam, mode):
    name = f"academy-gha-candidate-{mode}"
    role = iam.get_role(RoleName=name)["Role"]
    require(role["AssumeRolePolicyDocument"] == document(f"trust_candidate_{mode}.json"),
            "Candidate OIDC trust differs")
    require(role["MaxSessionDuration"] == 10800, "Candidate role session limit differs")
    attached = iam.list_attached_role_policies(RoleName=name)
    inline = iam.list_role_policies(RoleName=name)
    require(not attached.get("AttachedPolicies") and not attached.get("IsTruncated")
            and inline.get("PolicyNames") == ["academy-candidate"] and not inline.get("IsTruncated"),
            "Candidate role has additional privileges")
    actual = iam.get_role_policy(RoleName=name, PolicyName="academy-candidate")["PolicyDocument"]
    require(actual == document(f"policy_candidate_{mode}.json"), "Candidate policy differs")
    return {"role": name, "trust": "exact", "policy": "exact"}


def apply_role(iam, name, trust, policy, policy_name, managed_ssm=False):
    try:
        current = iam.get_role(RoleName=name)["Role"]
    except iam.exceptions.NoSuchEntityException:
        iam.create_role(RoleName=name, AssumeRolePolicyDocument=json.dumps(trust),
                        MaxSessionDuration=10800,
                        Tags=[{"Key": "Project", "Value": "academy"}, {"Key": "Owner", "Value": "candidate-foundation"}])
    else:
        tags = {x["Key"]: x["Value"] for x in current.get("Tags", [])}
        require(tags.get("Owner") == "candidate-foundation", "Existing role is not owned by this convergence")
        iam.update_assume_role_policy(RoleName=name, PolicyDocument=json.dumps(trust))
        iam.update_role(RoleName=name, MaxSessionDuration=10800)
    iam.put_role_policy(RoleName=name, PolicyName=policy_name, PolicyDocument=json.dumps(policy))
    if managed_ssm:
        iam.attach_role_policy(RoleName=name, PolicyArn="arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore")


def converge(iam, ecr, apply=False, lifecycle_plan=None, provision_environments=False):
    result = {"mode": "apply" if apply else "plan", "roles": [], "qa_repositories": [], "environments": {}}
    for mode in ("qa", "production"):
        if apply:
            if provision_environments:
                provision_environment(mode)
            verify_environment(mode)
            result["environments"][mode] = "main-only"
        else:
            try:
                verify_environment(mode)
                result["environments"][mode] = "main-only"
            except urllib.error.HTTPError as exc:
                if exc.code != 404:
                    raise
                result["environments"][mode] = "missing"
            except ValueError:
                result["environments"][mode] = "protection-mismatch"
        role = f"academy-gha-candidate-{mode}"
        if apply:
            apply_role(iam, role, document(f"trust_candidate_{mode}.json"),
                       document(f"policy_candidate_{mode}.json"), "academy-candidate")
            result["roles"].append(readback(iam, mode))
        else:
            result["roles"].append({"role": role, "policy": f"policy_candidate_{mode}.json"})
    qa_role = "academy-api-qa-role"
    trust = {"Version": "2012-10-17", "Statement": [{
        "Effect": "Allow", "Principal": {"Service": "ec2.amazonaws.com"}, "Action": "sts:AssumeRole"}]}
    if apply:
        apply_role(iam, qa_role, trust, document("policy_candidate_qa_runtime.json"),
                   "academy-candidate-runtime", managed_ssm=True)
        try:
            profile = iam.get_instance_profile(InstanceProfileName="academy-api-qa")["InstanceProfile"]
        except iam.exceptions.NoSuchEntityException:
            profile = iam.create_instance_profile(InstanceProfileName="academy-api-qa")["InstanceProfile"]
        roles = profile.get("Roles", [])
        require(not roles or [r["RoleName"] for r in roles] == [qa_role], "QA profile has an unrelated role")
        if not roles:
            iam.add_role_to_instance_profile(InstanceProfileName="academy-api-qa", RoleName=qa_role)
    result["qa_instance_profile"] = "academy-api-qa"
    if apply:
        # Add only independent nonproduction source access to the existing release role.
        iam.get_role(RoleName="academy-gha-ecr-build")
        expected = document("policy_candidate_promotion_sources.json")
        iam.put_role_policy(RoleName="academy-gha-ecr-build", PolicyName="academy-candidate-sources",
                            PolicyDocument=json.dumps(expected))
        actual = iam.get_role_policy(RoleName="academy-gha-ecr-build", PolicyName="academy-candidate-sources")["PolicyDocument"]
        require(actual == expected, "Promotion source policy readback differs")

    for role in IMAGE_NAMES:
        repo = role.replace("academy-", "academy-qa-", 1)
        if apply:
            try:
                value = ecr.describe_repositories(repositoryNames=[repo])["repositories"][0]
                require(value["imageTagMutability"] == "IMMUTABLE", "Existing QA repository is not immutable")
            except ecr.exceptions.RepositoryNotFoundException:
                ecr.create_repository(repositoryName=repo, imageTagMutability="IMMUTABLE",
                                      imageScanningConfiguration={"scanOnPush": True},
                                      tags=[{"Key": "Project", "Value": "academy"},
                                            {"Key": "Owner", "Value": "candidate-foundation"}])
        result["qa_repositories"].append(repo)
    result["native_lifecycle"] = {}
    for repo in list(IMAGE_NAMES) + result["qa_repositories"]:
        try:
            raw = ecr.get_lifecycle_policy(repositoryName=repo)["lifecyclePolicyText"]
        except (ecr.exceptions.LifecyclePolicyNotFoundException, ecr.exceptions.RepositoryNotFoundException):
            result["native_lifecycle"][repo] = {"state": "absent"}
            continue
        parsed = json.loads(raw)
        fingerprint = hashlib.sha256(json.dumps(parsed, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        result["native_lifecycle"][repo] = {"state": "present", "sha256": fingerprint, "policy": parsed}
        if apply:
            expected = (lifecycle_plan or {}).get("native_lifecycle", {}).get(repo, {})
            require(expected.get("sha256") == fingerprint and expected.get("policy") == parsed,
                    "Native lifecycle removal requires an exact reviewed plan")
            ecr.delete_lifecycle_policy(repositoryName=repo)
            try:
                ecr.get_lifecycle_policy(repositoryName=repo)
            except ecr.exceptions.LifecyclePolicyNotFoundException:
                result["native_lifecycle"][repo]["state"] = "removed-and-verified"
            else:
                raise ValueError("Native lifecycle removal readback failed")
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--apply", action="store_true")
    p.add_argument("--readback", action="store_true")
    p.add_argument("--provision-environments", action="store_true")
    p.add_argument("--lifecycle-plan", type=Path)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    import boto3
    try:
        require(boto3.client("sts").get_caller_identity()["Account"] == ACCOUNT, "Wrong AWS account")
        iam = boto3.client("iam")
        if a.readback:
            actual = iam.get_role_policy(RoleName="academy-api-qa-role", PolicyName="academy-candidate-runtime")["PolicyDocument"]
            require(actual == document("policy_candidate_qa_runtime.json"), "QA runtime policy differs")
            attached = iam.list_attached_role_policies(RoleName="academy-api-qa-role")
            require(not attached.get("IsTruncated") and [v["PolicyArn"] for v in attached.get("AttachedPolicies", [])] ==
                    ["arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"], "Unexpected QA runtime managed policy")
            policies = iam.list_role_policies(RoleName="academy-api-qa-role")
            require(not policies.get("IsTruncated") and policies.get("PolicyNames") == ["academy-candidate-runtime"],
                    "Unexpected QA runtime inline policy")
            result = {"roles": [readback(iam, mode) for mode in ("qa", "production")]}
            for mode in ("qa", "production"):
                verify_environment(mode)
        else:
            if a.apply:
                require(not subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True).strip(),
                        "Apply requires a clean checkout")
                head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
                require(head == github(f"repos/{REPOSITORY}/git/ref/heads/main")["object"]["sha"],
                        "Apply requires exact remote main")
                subprocess.run(["python3", str(ROOT / "scripts/v1/deployment_lock.py"), "assert-owned",
                                "--owner", os.environ["ACADEMY_DEPLOY_LOCK_OWNER"]], check=True)
            result = converge(iam, boto3.client("ecr", region_name=REGION), a.apply,
                              json.loads(a.lifecycle_plan.read_text()) if a.lifecycle_plan else None,
                              a.provision_environments)
        a.output.parent.mkdir(parents=True, exist_ok=True)
        a.output.write_text(json.dumps(result, indent=2) + "\n")
        print("CANDIDATE_PREREQUISITES_OK")
    except Exception:
        p.exit(2, "CANDIDATE_PREREQUISITES_BLOCKED: identity, ownership, main/environment policy or readback differs\n")


if __name__ == "__main__":
    main()
