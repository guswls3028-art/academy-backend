"""Trusted controller building blocks for inert candidate launch and PREPARED boot.

This module is not a workflow entry point. It cannot publish an endpoint, activate
a lease, migrate data, stop the baseline or terminate an instance. The workflow
must retain the launch plan before calling launch, then use trusted observations
and the existing Window protocol before making product access available.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from candidate_qa_window import PROFILE, binding, require, specification

ACCOUNT = "809466760795"
REGION = "ap-northeast-2"
REGISTRY = f"{ACCOUNT}.dkr.ecr.{REGION}.amazonaws.com"
ROLE_NAMES = {"api": "api", "tools": "tools-worker", "ai": "ai-worker-cpu", "messaging": "messaging-worker"}
CONTAINERS = {"api": "academy-api", "tools": "academy-tools-development",
              "ai": "academy-ai-development", "messaging": "academy-messaging-development"}
IDENTITY_FILE = "/opt/academy-qa/required.env"

INERT_USER_DATA = """#!/bin/bash
set -euo pipefail
umask 077
# No environment/key retrieval, images, containers, migration or queue polling.
dnf install -y docker
systemctl enable --now docker
install -d -m 0700 /opt/academy-qa
printf '%s\\n' 'ACADEMY_QA_MODE=isolated-qa' 'CANDIDATE_LEASE_REQUIRED=true' > /opt/academy-qa/required.env
chmod 0600 /opt/academy-qa/required.env
test -z "$(docker ps -aq)"
touch /opt/academy-qa/inert-ready
"""

# Fixed trusted-controller program, not a command supplied by the candidate.
START_PROGRAM = r'''
import base64, json, os, re, subprocess, sys
from pathlib import Path

def run(args):
    result = subprocess.run(args, input=None, text=True, capture_output=True, timeout=240)
    if result.returncode:
        raise RuntimeError("QA bootstrap command failed")
    return result.stdout

def main():
    plan = json.loads(base64.b64decode(sys.argv[1], validate=True))
    assert Path("/opt/academy-qa/inert-ready").is_file()
    assert Path("/opt/academy-qa/required.env").read_text().splitlines() == [
        "ACADEMY_QA_MODE=isolated-qa", "CANDIDATE_LEASE_REQUIRED=true"]
    assert not run(["docker", "ps", "-aq"]).strip()
    os.umask(0o077)
    env_files = {}
    for kind, source in plan["environments"].items():
        response = json.loads(run(["aws", "ssm", "get-parameter", "--name", source["name"],
            "--with-decryption", "--region", "ap-northeast-2", "--output", "json"]))
        parameter = response["Parameter"]
        assert parameter["Version"] == source["version"]
        value = parameter["Value"]
        if kind == "workers":
            value = base64.b64decode(value, validate=True).decode()
        value = json.loads(value)
        assert isinstance(value, dict)
        assert all(re.fullmatch(r"[A-Z][A-Z0-9_]*", key) and isinstance(item, str)
                   and not any(c in item for c in "\r\n\x00") for key, item in value.items())
        assert value["ACADEMY_RUNTIME_ENV"] == "development"
        assert value["ACADEMY_DEVELOPMENT_RELEASE_ID"] == plan["release_id"]
        assert value["DB_NAME"] == "academy_api_development"
        assert value["DB_USER"] == "academy_api_development_app"
        assert value["SOLAPI_MOCK"].lower() == "true"
        assert value["TOSS_AUTO_BILLING_ENABLED"].lower() == "false"
        assert not value["VIDEO_BATCH_JOB_QUEUE"] and not value["VIDEO_BATCH_JOB_DEFINITION"]
        assert value["TOOLS_SQS_QUEUE_NAME"] == "academy-v1-development-tools-queue"
        assert value["MESSAGING_SQS_QUEUE_NAME"] == "academy-v1-development-messaging-queue"
        for tier in ("LITE", "BASIC", "PREMIUM"):
            assert value["AI_SQS_QUEUE_NAME_" + tier] == "academy-v1-development-ai-queue"
        assert value["DJANGO_SETTINGS_MODULE"] == (
            "apps.api.config.settings.development" if kind == "api" else "apps.api.config.settings.worker")
        assert not any(key.startswith("ACADEMY_QA_") or key == "CANDIDATE_LEASE_REQUIRED" for key in value)
        if kind == "api":
            assert not value.get("GEMINI_API_KEY")
        value.update(plan["identity"])
        path = Path("/opt/academy-qa/" + kind + ".env")
        path.write_text("".join(key + "=" + item + "\n" for key, item in sorted(value.items())))
        path.chmod(0o600)
        env_files[kind] = str(path)
    # The existing development environment requires a host-local cache.
    try:
        run(["dnf", "install", "-y", "redis6"])
        cache = "redis6"
    except RuntimeError:
        run(["dnf", "install", "-y", "valkey"])
        cache = "valkey"
    run(["systemctl", "enable", "--now", cache])
    assert run([cache + "-cli", "ping"]).strip() == "PONG"
    password = run(["aws", "ecr", "get-login-password", "--region", "ap-northeast-2"])
    logged_in = subprocess.run(["docker", "login", "--username", "AWS", "--password-stdin",
        plan["registry"]], input=password, text=True, capture_output=True, timeout=60)
    if logged_in.returncode:
        raise RuntimeError("QA image registry authentication failed")
    for kind, item in plan["containers"].items():
        run(["docker", "pull", item["image"]])
        args = ["docker", "run", "-d", "--restart", "unless-stopped", "--network", "host",
                "--name", item["name"], "--env-file", env_files["api" if kind == "api" else "workers"]]
        if kind == "ai":
            args += ["-e", "AI_WORKER_IDLE_SCALE_IN_ENABLED=0", "-e", "EC2_IDLE_STOP_THRESHOLD=0"]
        run(args + [item["image"]])
    print(json.dumps({"status": "PREPARED_CONTAINERS_STARTED", "lease_id": plan["identity"]["ACADEMY_QA_LEASE_ID"]}))

try:
    main()
except Exception:
    # Never echo source environments, AWS/docker stderr or credential values.
    print('{"status":"QA_BOOTSTRAP_HOLD"}')
    raise SystemExit(2)
'''


def image_uris(images):
    require(set(images) == set(ROLE_NAMES), "Exact four runtime images required")
    require(all(isinstance(v, str) and re.fullmatch(r"sha256:[0-9a-f]{64}", v)
                for v in images.values()), "Immutable runtime digests required")
    return {kind: f"{REGISTRY}/academy-qa-{ROLE_NAMES[kind]}@{digest}"
            for kind, digest in images.items()}


def start_plan(record, *, release_id, api_version, workers_version):
    specification(**{key: record[key] for key in (
        "lease_id", "owner_task", "lock_owner", "source_sha", "images", "endpoint",
        "profile", "scope", "baseline_sha256", "tenant_ids", "message_key_version")})
    require(record["state"] == "prepared" and not record.get("control_hold"), "Only PREPARED may bootstrap")
    require(re.fullmatch(r"sha-" + record["source_sha"] + r"-run-[1-9][0-9]*-[1-9][0-9]*", release_id),
            "Release/source provenance differs")
    require(release_id.split("-run-", 1)[1].replace("-", ":") == record["lock_owner"].split(":", 1)[1],
            "Release attempt differs from the lease")
    require(all(type(v) is int and v > 0 for v in (api_version, workers_version)), "Pinned environment versions required")
    identity = {
        "ACADEMY_QA_MODE": "isolated-qa", "CANDIDATE_LEASE_REQUIRED": "true",
        "ACADEMY_QA_LEASE_ID": record["lease_id"], "ACADEMY_QA_BINDING_SHA256": binding(record),
        "ACADEMY_QA_MESSAGE_KEY_VERSION": str(record["message_key_version"]),
    }
    return {"registry": REGISTRY, "release_id": release_id, "identity": identity,
            "environments": {
                "api": {"name": f"/academy/api/development/env:{api_version}", "version": api_version},
                "workers": {"name": f"/academy/workers/development/env:{workers_version}", "version": workers_version},
            },
            "containers": {kind: {"name": CONTAINERS[kind], "image": uri}
                           for kind, uri in image_uris(record["images"]).items()}}


def start_command(plan):
    import base64
    encoded = base64.b64encode(json.dumps(plan, sort_keys=True).encode()).decode()
    return "python3 - '" + encoded + "' <<'ACADEMY_QA_BOOTSTRAP'\n" + START_PROGRAM + "\nACADEMY_QA_BOOTSTRAP"


def assert_main_controller(root=None):
    from candidate_manifest import github, REPOSITORY
    root = root or Path(__file__).resolve().parents[2]
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    dirty = subprocess.check_output(["git", "status", "--porcelain"], cwd=root, text=True).strip()
    require(not dirty and head == github(f"repos/{REPOSITORY}/git/ref/heads/main")["object"]["sha"],
            "Trusted controller must be clean exact remote main")
    return head


class InertLauncher:
    """No implicit activation, cleanup, lease creation, or baseline replacement."""
    def __init__(self, *, ec2, ssm, sts, store, clock):
        self.ec2, self.ssm, self.sts, self.store, self.clock = ec2, ssm, sts, store, clock

    def _authority(self, lock_owner):
        assert_main_controller()
        identity = self.sts.get_caller_identity()
        require(identity["Account"] == ACCOUNT and re.fullmatch(
            rf"arn:aws:sts::{ACCOUNT}:assumed-role/academy-gha-candidate-production/[^/]+",
            identity["Arn"]), "Trusted candidate-production identity required")
        lock = self.store.client.get_item(TableName=self.store.table,
            Key={"videoId": {"S": "__deployment_control_v2__"}}, ConsistentRead=True).get("Item", {})
        require(lock.get("owner", {}).get("S") == lock_owner
                and int(lock.get("ttl", {}).get("N", "0")) > int(self.clock()), "Live owned mutation lock required")

    def start(self, record, *, release_id, api_version, workers_version):
        self._authority(record["lock_owner"])
        current = self.store.read()
        require(current == record and int(self.clock()) < record["expires_at"] - 30,
                "Prepared lease changed or expired")
        plan = start_plan(record, release_id=release_id, api_version=api_version, workers_version=workers_version)
        instance_id = record["endpoint"].split("//", 1)[1].split(":")[0]
        result = self.ec2.describe_instances(InstanceIds=[instance_id])
        instances = [i for r in result.get("Reservations", []) for i in r.get("Instances", [])]
        require(len(instances) == 1 and not result.get("NextToken"), "Exact prepared instance required")
        instance = instances[0]
        tags = {v["Key"]: v["Value"] for v in instance.get("Tags", [])}
        require(instance["State"]["Name"] == "running"
                and instance.get("IamInstanceProfile", {}).get("Arn") == PROFILE
                and tags.get("QaMode") == "isolated-qa"
                and tags.get("SlotLeaseOwner") == record["owner_task"]
                and tags.get("CandidateLeaseId") == record["lease_id"]
                and tags.get("CandidateSourceSha") == record["source_sha"]
                and tags.get("ReleaseId") == release_id,
                "Prepared runtime ownership differs")
        # Recheck immediately before submission. Uncertain/partial SSM execution
        # retains all containers and baseline; never retry by deleting containers.
        self._authority(record["lock_owner"])
        require(self.store.read() == record, "Prepared lease changed before bootstrap")
        return self.ssm.send_command(InstanceIds=[instance_id], DocumentName="AWS-RunShellScript",
            Parameters={"commands": [start_command(plan)], "executionTimeout": ["600"]},
            TimeoutSeconds=600, Comment="Start exact PREPARED candidate containers")["Command"]["CommandId"]

    def launch(self, *, baseline_path, baseline_sha256, owner_task, lease_id,
               source_sha, images, release_id, api_version, workers_version):
        """Retain the existing healthy baseline; create only inert owned capacity."""
        from candidate_slot import load_snapshot, guard, baseline_matches
        import yaml
        baseline = load_snapshot(Path(baseline_path), baseline_sha256)
        require(baseline["owner"] == owner_task
                and re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", owner_task),
                "The trusted baseline must bind the exact task UUID")
        require(re.fullmatch(r"[0-9a-f]{32}", lease_id)
                and re.fullmatch(r"[0-9a-f]{40}", source_sha), "Exact candidate identity required")
        lock_owner = baseline["lock_owner"]
        require(release_id == "sha-" + source_sha + "-run-" + lock_owner.split(":", 1)[1].replace(":", "-"),
                "Candidate source/attempt differs")
        require(all(type(v) is int and v > 0 for v in (api_version, workers_version)),
                "Pinned environment versions required")
        uris = image_uris(images)
        self._authority(lock_owner)
        # A lost RunInstances acknowledgement must never create another host.
        prior = self.ec2.describe_instances(Filters=[{"Name": "client-token", "Values": [lease_id]}])
        require(not prior.get("NextToken"), "Incomplete launch recovery inventory")
        prior_instances = [i for r in prior.get("Reservations", []) for i in r.get("Instances", [])]
        require(len(prior_instances) <= 1, "Ambiguous launch recovery inventory")
        expected_tags = {
            "Name": "academy-v1-api-development", "ManagedBy": "academy-api-development",
            "Environment": "development", "Project": "academy", "Lifecycle": "candidate",
            "QaMode": "isolated-qa", "SlotLeaseOwner": owner_task,
            "CandidateLeaseId": lease_id, "CandidateSourceSha": source_sha,
            "CandidateBaselineSha256": baseline_sha256, "ReleaseId": release_id,
            "ApiImageUri": uris["api"], "ToolsImageUri": uris["tools"],
            "AiImageUri": uris["ai"], "MessagingImageUri": uris["messaging"],
            "ApiEnvVersion": str(api_version), "WorkersEnvVersion": str(workers_version),
            "ProductionDatabase": baseline["production_database"],
        }
        if prior_instances:
            instance = prior_instances[0]
            tags = {v["Key"]: v["Value"] for v in instance.get("Tags", [])}
            require(all(tags.get(k) == v for k, v in expected_tags.items())
                    and instance.get("IamInstanceProfile", {}).get("Arn") == PROFILE
                    and instance.get("State", {}).get("Name") in {"pending", "running"},
                    "Existing launch differs; retain exact resource for recovery")
            return instance["InstanceId"]
        baseline_instances = guard(owner_task, require_baseline=True)
        require(len(baseline_instances) == 1 and baseline_matches(baseline, baseline_instances[0])
                and baseline_instances[0]["InstanceId"] == baseline["instance_id"],
                "Captured baseline identity changed")
        config = yaml.safe_load((Path(__file__).resolve().parents[2] / "docs/ssot/params.yaml").read_text())
        require(baseline["ami"] == config["api"]["amiId"]
                and baseline["instance_type"] == config["api"]["instanceType"],
                "Baseline compute differs from trusted SSOT")
        original = baseline_instances[0]
        require(original["VpcId"] == config["network"]["vpcId"], "Baseline VPC differs")
        groups = self.ec2.describe_security_groups(Filters=[
            {"Name": "vpc-id", "Values": [original["VpcId"]]},
            {"Name": "group-name", "Values": [config["apiDevelopment"]["securityGroupName"]]},
        ])
        require(not groups.get("NextToken") and len(groups.get("SecurityGroups", [])) == 1,
                "Exact isolated security group required")
        group = groups["SecurityGroups"][0]
        require(not group.get("IpPermissions") and group["VpcId"] == original["VpcId"],
                "QA security group permits inbound access")
        self._authority(lock_owner)
        result = self.ec2.run_instances(
            ImageId=baseline["ami"], InstanceType=baseline["instance_type"],
            IamInstanceProfile={"Arn": PROFILE}, MinCount=1, MaxCount=1,
            NetworkInterfaces=[{"DeviceIndex": 0, "SubnetId": original["SubnetId"],
                "Groups": [group["GroupId"]], "AssociatePublicIpAddress": True, "DeleteOnTermination": True}],
            MetadataOptions={"HttpTokens": "required", "HttpEndpoint": "enabled",
                             "HttpPutResponseHopLimit": 2},
            InstanceInitiatedShutdownBehavior="stop", ClientToken=lease_id,
            TagSpecifications=[{"ResourceType": "instance", "Tags": [
                {"Key": key, "Value": value} for key, value in sorted(expected_tags.items())]}],
            UserData=INERT_USER_DATA,
        )
        instances = result.get("Instances", [])
        require(len(instances) == 1 and re.fullmatch(r"i-[0-9a-f]{17}", instances[0].get("InstanceId", "")),
                "Launch acknowledgement incomplete; recover by exact client token")
        return instances[0]["InstanceId"]
