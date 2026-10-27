"""Owned, isolated Fargate runtime and real-codec canary; no production writes.

The PowerShell entrypoint supplies the existing SSOT loader's public configuration.
AWS CLI is the only dependency. Secret parameter values never enter this process.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid

from deployment_lock import DEFAULT_TABLE, assert_owned

MANAGED_BY = "academy-homework-preview-development"
POLICY_NAME = "academy-homework-preview-development"


def container_properties_match(actual: dict, expected: dict) -> bool:
    """Batch adds an empty log secret list on readback; nonempty grants are drift."""
    actual = deepcopy(actual)
    for field in ("volumes", "mountPoints", "ulimits", "secrets"):
        if actual.get(field, []) != expected.get(field, []):
            return False
    log = actual.get("logConfiguration", {})
    if log.get("secretOptions") == [] and "secretOptions" not in expected.get("logConfiguration", {}):
        del log["secretOptions"]
    return all(actual.get(key) == value for key, value in expected.items())


def definition_name(release: str, version: int) -> str:
    if not re.fullmatch(r"(?:sha-[0-9a-f]{40}-run-[0-9]+-[0-9]+|manual-sha256-[0-9a-f]{64})", release) or version < 1:
        raise ValueError("invalid_candidate_identity")
    return f"academy-development-homework-video-{hashlib.sha256(release.encode()).hexdigest()[:12]}-v{version}"


class Runtime:
    def __init__(self, config: dict, image: str, release: str, version: int, apply: bool):
        self.config, self.image, self.release, self.version, self.apply = config, image, release, version, apply
        self.region, self.account = config["global"]["region"], str(config["global"]["accountId"])
        self.development, self.preview = config["apiDevelopment"], config["homeworkPreviewDevelopment"]
        self.name = definition_name(release, version)
        self.tags = {"Project": "academy", "Environment": "development", "ManagedBy": MANAGED_BY}
        self.owner = os.environ.get("ACADEMY_DEPLOY_LOCK_OWNER", "")
        self.evidence = {}
        expected = f"{self.account}.dkr.ecr.{self.region}.amazonaws.com/academy-video-worker@sha256:"
        if not image.startswith(expected) or not re.fullmatch(r"[a-f0-9]{64}", image[len(expected):]):
            raise ValueError("immutable_video_image_required")
        if (self.preview["queueName"] != "academy-development-homework-video-queue"
                or self.preview["jobDefinitionPrefix"] != "academy-development-homework-video-"
                or self.development["workersEnvParameter"] != "/academy/workers/development/env"
                or str(self.preview["maxVcpus"]) != "4"
                or self.development["accessMode"] != "ssm-only"):
            raise ValueError("isolated_runtime_contract_mismatch")
        for key, expected in {"computeEnvironmentName": "academy-development-homework-video-ce",
                "jobRoleName": "academy-development-homework-video-job-role",
                "executionRoleName": "academy-development-homework-video-execution-role",
                "apiPolicyName": POLICY_NAME, "logGroupName": "/aws/batch/academy-homework-development"}.items():
            if self.preview[key] != expected:
                raise ValueError("owned_development_resource_required")
        if self.development["roleName"] != "academy-api-development-role":
            raise ValueError("dedicated_api_role_required")

    def aws(self, service: str, operation: str, payload: dict | None = None, *, mutate=False) -> dict:
        if mutate:
            if not self.apply or not self.owner:
                raise RuntimeError("apply_with_shared_lock_required")
            assert_owned(DEFAULT_TABLE, self.owner)
        completed = subprocess.run(["aws", service, operation, "--cli-input-json", json.dumps(payload or {}),
                                    "--region", self.region, "--output", "json"], capture_output=True, text=True)
        if completed.returncode:
            # A closed error code, never CLI output (SSM/connection errors can contain secrets).
            code = re.search(r"An error occurred \(([^)]+)\)", completed.stderr)
            raise RuntimeError(f"{service}:{operation}:{code.group(1) if code else 'command_failed'}")
        return json.loads(completed.stdout or "{}")

    def arn(self, service: str, resource: str) -> str:
        return f"arn:aws:{service}:{'' if service == 'iam' else self.region}:{self.account}:{resource}"

    def owned(self, row: dict, key="tags") -> None:
        tags = row.get(key, {})
        if isinstance(tags, list):
            tags = {item["Key"]: item["Value"] for item in tags}
        if any(tags.get(k) != v for k, v in self.tags.items()):
            raise RuntimeError("existing_resource_ownership_mismatch")

    def discover(self) -> dict:
        if self.aws("sts", "get-caller-identity")["Account"] != self.account:
            raise RuntimeError("aws_account_mismatch")
        self.aws("ecr", "describe-images", {"repositoryName": "academy-video-worker",
                 "imageIds": [{"imageDigest": self.image.split("@", 1)[1]}]})
        groups = self.aws("ec2", "describe-security-groups", {"Filters": [
            {"Name": "vpc-id", "Values": [self.config["network"]["vpcId"]]},
            {"Name": "group-name", "Values": [self.development["securityGroupName"]]}]})["SecurityGroups"]
        if len(groups) != 1 or groups[0]["IpPermissions"]:
            raise RuntimeError("ssm_only_development_security_group_required")
        cidrs = [self.config["network"][f"publicSubnetCidr{i}"] for i in (1, 2)]
        subnets = self.aws("ec2", "describe-subnets", {"Filters": [
            {"Name": "vpc-id", "Values": [self.config["network"]["vpcId"]]},
            {"Name": "cidr-block", "Values": cidrs}]})["Subnets"]
        if len(subnets) != 2 or len({s["AvailabilityZone"] for s in subnets}) != 2:
            raise RuntimeError("two_existing_public_subnets_required")
        routes = self.aws("ec2", "describe-route-tables", {"Filters": [
            {"Name": "vpc-id", "Values": [self.config["network"]["vpcId"]]}]})["RouteTables"]
        for subnet in subnets:
            explicit = [r for r in routes if any(a.get("SubnetId") == subnet["SubnetId"] for a in r["Associations"])]
            tables = explicit or [r for r in routes if any(a.get("Main") for a in r["Associations"])]
            if len(tables) != 1 or not any(r.get("DestinationCidrBlock") == "0.0.0.0/0" and
                    r.get("GatewayId", "").startswith("igw-") and r.get("State") == "active" for r in tables[0]["Routes"]):
                raise RuntimeError("public_subnet_egress_unproven")
        return {"security_group": groups[0]["GroupId"], "subnets": sorted(s["SubnetId"] for s in subnets)}

    def role(self, name: str, statements: list[dict]) -> str:
        trust = {"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Principal": {"Service": "ecs-tasks.amazonaws.com"},
            "Action": "sts:AssumeRole", "Condition": {"StringEquals": {"aws:SourceAccount": self.account},
                "ArnLike": {"aws:SourceArn": self.arn("ecs", "*")}}}]}
        try:
            role = self.aws("iam", "get-role", {"RoleName": name})["Role"]
        except RuntimeError as error:
            if not str(error).endswith(":NoSuchEntity"):
                raise
            role = self.aws("iam", "create-role", {"RoleName": name, "AssumeRolePolicyDocument": json.dumps(trust),
                "Tags": [{"Key": k, "Value": v} for k, v in self.tags.items()]}, mutate=True)["Role"]
        self.owned(role, "Tags")
        attached = self.aws("iam", "list-attached-role-policies", {"RoleName": name})["AttachedPolicies"]
        inline = self.aws("iam", "list-role-policies", {"RoleName": name})["PolicyNames"]
        if attached or set(inline) - {POLICY_NAME}:
            raise RuntimeError("unexpected_development_role_permissions")
        if role["AssumeRolePolicyDocument"] != trust:
            raise RuntimeError("existing_development_role_trust_mismatch")
        policy = {"Version": "2012-10-17", "Statement": statements}
        self.aws("iam", "put-role-policy", {"RoleName": name, "PolicyName": POLICY_NAME,
                 "PolicyDocument": json.dumps(policy)}, mutate=True)
        if self.aws("iam", "get-role-policy", {"RoleName": name, "PolicyName": POLICY_NAME})["PolicyDocument"] != policy:
            raise RuntimeError("role_policy_readback_mismatch")
        return role["Arn"]

    def converge(self, network: dict) -> dict:
        parameter = self.arn("ssm", "parameter" + self.development["workersEnvParameter"])
        job_role = self.role(self.preview["jobRoleName"], [
            {"Effect": "Allow", "Action": "ssm:GetParameter", "Resource": parameter},
            {"Effect": "Deny", "Action": ["ssm:GetParameter", "ssm:GetParameters"], "NotResource": parameter},
            {"Effect": "Deny", "Action": ["ssm:GetParameterHistory", "ssm:GetParametersByPath", "ssm:DescribeParameters"], "Resource": "*"}])
        logs = self.preview["logGroupName"]
        existing_logs = self.aws("logs", "describe-log-groups", {"logGroupNamePrefix": logs})["logGroups"]
        if not any(g["logGroupName"] == logs for g in existing_logs):
            self.aws("logs", "create-log-group", {"logGroupName": logs, "tags": self.tags}, mutate=True)
        if self.aws("logs", "list-tags-log-group", {"logGroupName": logs})["tags"] != self.tags:
            raise RuntimeError("development_log_group_ownership_mismatch")
        self.aws("logs", "put-retention-policy", {"logGroupName": logs, "retentionInDays": 14}, mutate=True)
        execution_role = self.role(self.preview["executionRoleName"], [
            {"Effect": "Allow", "Action": "ecr:GetAuthorizationToken", "Resource": "*"},
            {"Effect": "Allow", "Action": ["ecr:BatchCheckLayerAvailability", "ecr:GetDownloadUrlForLayer", "ecr:BatchGetImage"],
                "Resource": self.arn("ecr", "repository/academy-video-worker")},
            {"Effect": "Allow", "Action": ["logs:CreateLogStream", "logs:PutLogEvents"],
                "Resource": self.arn("logs", f"log-group:{logs}:*")}])
        ce_name, queue_name = self.preview["computeEnvironmentName"], self.preview["queueName"]
        resources = {"type": "FARGATE", "maxvCpus": 4, "subnets": network["subnets"], "securityGroupIds": [network["security_group"]]}
        ce = self.aws("batch", "describe-compute-environments", {"computeEnvironments": [ce_name]})["computeEnvironments"]
        if not ce:
            self.aws("batch", "create-compute-environment", {"computeEnvironmentName": ce_name, "type": "MANAGED", "state": "ENABLED",
                "computeResources": resources, "tags": self.tags}, mutate=True)
        deadline = time.monotonic() + 300
        while True:
            ce = self.aws("batch", "describe-compute-environments", {"computeEnvironments": [ce_name]})["computeEnvironments"]
            if ce:
                self.owned(ce[0])
                actual = ce[0]["computeResources"]
                if any(sorted(actual.get(k, [])) != sorted(v) if isinstance(v, list) else actual.get(k) != v for k, v in resources.items()):
                    raise RuntimeError("existing_compute_environment_drift")
                if ce[0]["status"] == "VALID" and ce[0]["state"] == "ENABLED":
                    break
                if ce[0]["status"] == "INVALID":
                    raise RuntimeError("development_compute_environment_invalid")
            if time.monotonic() >= deadline:
                raise TimeoutError("development_compute_environment_pending")
            time.sleep(5)
        order = [{"order": 1, "computeEnvironment": ce[0]["computeEnvironmentArn"]}]
        queues = self.aws("batch", "describe-job-queues", {"jobQueues": [queue_name]})["jobQueues"]
        if not queues:
            self.aws("batch", "create-job-queue", {"jobQueueName": queue_name, "state": "ENABLED", "priority": 1,
                "computeEnvironmentOrder": order, "tags": self.tags}, mutate=True)
        deadline = time.monotonic() + 180
        while True:
            queues = self.aws("batch", "describe-job-queues", {"jobQueues": [queue_name]})["jobQueues"]
            if queues:
                self.owned(queues[0])
                if queues[0]["computeEnvironmentOrder"] != order or queues[0]["priority"] != 1:
                    raise RuntimeError("existing_development_queue_drift")
                if queues[0]["status"] == "VALID" and queues[0]["state"] == "ENABLED":
                    break
            if time.monotonic() >= deadline:
                raise TimeoutError("development_queue_pending")
            time.sleep(5)
        properties = {"image": self.image, "executionRoleArn": execution_role, "jobRoleArn": job_role,
            "command": ["python", "-m", "apps.worker.video_worker.homework_preview_main"],
            "resourceRequirements": [{"type": "VCPU", "value": "2"}, {"type": "MEMORY", "value": "4096"}],
            "environment": [{"name": "BATCH_SSM_ENV", "value": f"{self.development['workersEnvParameter']}:{self.version}"},
                            {"name": "AWS_DEFAULT_REGION", "value": self.region}],
            "networkConfiguration": {"assignPublicIp": "ENABLED"},
            "runtimePlatform": {"cpuArchitecture": "ARM64", "operatingSystemFamily": "LINUX"},
            "fargatePlatformConfiguration": {"platformVersion": "LATEST"},
            "logConfiguration": {"logDriver": "awslogs", "options": {"awslogs-group": logs,
                "awslogs-region": self.region, "awslogs-stream-prefix": "homework"}}}
        definitions = self.aws("batch", "describe-job-definitions", {"jobDefinitionName": self.name})["jobDefinitions"]
        if not definitions:
            self.aws("batch", "register-job-definition", {"jobDefinitionName": self.name, "type": "container", "platformCapabilities": ["FARGATE"],
                "containerProperties": properties, "timeout": {"attemptDurationSeconds": 900}, "retryStrategy": {"attempts": 1},
                "tags": self.tags, "propagateTags": True}, mutate=True)
            definitions = self.aws("batch", "describe-job-definitions", {"jobDefinitionName": self.name})["jobDefinitions"]
        if len(definitions) != 1 or definitions[0]["revision"] != 1 or definitions[0]["status"] != "ACTIVE":
            raise RuntimeError("immutable_development_definition_conflict")
        definition = definitions[0]
        self.owned(definition)
        if (not container_properties_match(definition["containerProperties"], properties)
                or definition["platformCapabilities"] != ["FARGATE"] or definition["timeout"]["attemptDurationSeconds"] != 900
                or definition["retryStrategy"]["attempts"] != 1):
            raise RuntimeError("immutable_development_definition_mismatch")
        template = Path(__file__).parent / "templates/iam/policy_homework_preview_development_submit.json"
        policy = json.loads(template.read_text().replace("__REGION__", self.region).replace("__ACCOUNT_ID__", self.account))
        api_role = self.development["roleName"]
        self.aws("iam", "put-role-policy", {"RoleName": api_role, "PolicyName": POLICY_NAME, "PolicyDocument": json.dumps(policy)}, mutate=True)
        if self.aws("iam", "get-role-policy", {"RoleName": api_role, "PolicyName": POLICY_NAME})["PolicyDocument"] != policy:
            raise RuntimeError("development_submit_policy_readback_mismatch")
        return {"queue": queues[0]["jobQueueArn"], "definition": definition["jobDefinitionArn"], "image": self.image,
                "release_id": self.release, "workers_parameter_version": self.version}

    def canary(self, runtime: dict, production_database: str) -> dict:
        if not re.fullmatch(r"[a-z][a-z0-9_]{2,62}", production_database) or production_database == "academy_api_development":
            raise ValueError("production_database_identity_required")
        instances = self.aws("ec2", "describe-instances", {"Filters": [
            {"Name": "tag:Name", "Values": [self.development["instanceName"]]},
            {"Name": "tag:ManagedBy", "Values": [self.development["managedByTag"]]},
            {"Name": "tag:Environment", "Values": ["development"]},
            {"Name": "tag:Lifecycle", "Values": ["active"]},
            {"Name": "instance-state-name", "Values": ["running"]}]})["Reservations"]
        targets = [i for r in instances for i in r["Instances"]]
        if len(targets) != 1:
            raise RuntimeError("one_active_development_instance_required")
        # API instance role, not the orchestration role, proves the product's SubmitJob permission.
        payload = {"jobName": "qa-homework-preview-" + uuid.uuid4().hex, "jobQueue": runtime["queue"], "jobDefinition": runtime["definition"],
            "containerOverrides": {"command": ["python", "-m", "apps.worker.video_worker.homework_preview_canary", self.release, production_database]},
            "tags": {**self.tags, "Purpose": "codec-canary"}, "timeout": {"attemptDurationSeconds": 300}}
        code = "import boto3,json; r=boto3.client('batch',region_name=" + repr(self.region) + ").submit_job(**" + repr(payload) + "); print(json.dumps({'job_id':r['jobId']}))"
        command = "set -eu\ntimeout 60 docker exec -i academy-api python - <<'HOMEWORK_CANARY'\n" + code + "\nHOMEWORK_CANARY"
        self.instance_id = targets[0]["InstanceId"]
        try:
            job_id = self.remote_json(command)["job_id"]
        except RuntimeError:
            # SubmitJob has no client token. Recover this exact unique name after
            # an uncertain SSM response instead of issuing a duplicate job.
            rows = self.aws("batch", "list-jobs", {"jobQueue": runtime["queue"],
                "filters": [{"name": "JOB_NAME", "values": [payload["jobName"]]}]})["jobSummaryList"]
            if len(rows) != 1:
                raise RuntimeError("canary_submission_unresolved:" + payload["jobName"]) from None
            job_id = rows[0]["jobId"]
        if not re.fullmatch(r"[0-9a-f-]{36}", job_id):
            raise RuntimeError("invalid_batch_identity")
        self.evidence["job_id"] = job_id
        try:
            return self.verify_job(runtime, job_id)
        finally:
            job = self.aws("batch", "describe-jobs", {"jobs": [job_id]})["jobs"][0]
            if job["status"] not in {"SUCCEEDED", "FAILED"}:
                raise RuntimeError("canary_not_terminal_cleanup_unsafe:" + job_id)
            cleanup = self.remote_json(self.cleanup_command(job_id))
            if cleanup != {"remaining_objects": 0, "tenant_rows_created": 0, "user_rows_created": 0}:
                raise RuntimeError("canary_cleanup_unproven")
            self.evidence["cleanup"] = cleanup

    @staticmethod
    def cleanup_command(job_id: str) -> str:
        if not re.fullmatch(r"[0-9a-f-]{36}", job_id):
            raise ValueError("invalid_owned_job_id")
        code = '''import os,json,django
django.setup()
from django.conf import settings
from apps.infrastructure.storage.r2 import _get_s3_client
assert os.environ.get('ACADEMY_RUNTIME_ENV') == 'development'
assert settings.DATABASES['default']['NAME'] == 'academy_api_development'
assert settings.R2_AI_BUCKET == 'academy-development-artifacts'
storage = _get_s3_client(timeout_seconds=15)
prefix = 'qa-homework-preview/JOB_ID/'
for name in ('source.mp4', 'preview.mp4'):
    storage.delete_object(Bucket=settings.R2_AI_BUCKET, Key=prefix + name)
remaining = storage.list_objects_v2(Bucket=settings.R2_AI_BUCKET, Prefix=prefix).get('KeyCount', 0)
assert remaining == 0
print(json.dumps({'remaining_objects': 0, 'tenant_rows_created': 0, 'user_rows_created': 0}))
'''.replace("JOB_ID", job_id)
        return "set -eu\ntimeout 90 docker exec -i academy-api python - <<'HOMEWORK_CANARY'\n" + code + "\nHOMEWORK_CANARY"

    def remote_json(self, command: str) -> dict:
        invocation = self.aws("ssm", "send-command", {"InstanceIds": [self.instance_id], "DocumentName": "AWS-RunShellScript",
            "Parameters": {"commands": [command], "executionTimeout": ["90"]}, "TimeoutSeconds": 120,
            "Comment": "Isolated synthetic homework codec canary via development API role"}, mutate=True)
        command_id = invocation["Command"]["CommandId"]
        deadline = time.monotonic() + 150
        while True:
            try:
                response = self.aws("ssm", "get-command-invocation", {"CommandId": command_id, "InstanceId": self.instance_id})
            except RuntimeError as error:
                if not str(error).endswith(":InvocationDoesNotExist"):
                    raise
                response = {}
            if response.get("Status") == "Success":
                # Django bootstrap may print nonsecret warnings; accept exactly
                # one complete JSON record and never relay raw remote output.
                rows = []
                for line in response["StandardOutputContent"].splitlines():
                    try:
                        rows.append(json.loads(line))
                    except ValueError:
                        continue
                if len(rows) != 1 or not isinstance(rows[0], dict):
                    raise RuntimeError("canary_remote_result_invalid")
                return rows[0]
            if response.get("Status") in {"Cancelled", "TimedOut", "Failed"} or time.monotonic() > deadline:
                raise RuntimeError("development_role_submit_canary_failed")
            time.sleep(3)

    def verify_job(self, runtime: dict, job_id: str) -> dict:
        deadline = time.monotonic() + 900
        while True:
            job = self.aws("batch", "describe-jobs", {"jobs": [job_id]})["jobs"][0]
            if job["jobDefinition"] != runtime["definition"] or job["jobQueue"] != runtime["queue"]:
                raise RuntimeError("canary_job_identity_mismatch")
            if job["status"] in {"SUCCEEDED", "FAILED"}:
                break
            if time.monotonic() > deadline:
                self.aws("batch", "terminate-job", {"jobId": job_id, "reason": "Isolated homework canary deadline"}, mutate=True)
                for _ in range(60):
                    job = self.aws("batch", "describe-jobs", {"jobs": [job_id]})["jobs"][0]
                    if job["status"] in {"SUCCEEDED", "FAILED"}:
                        break
                    time.sleep(2)
                raise TimeoutError("canary_deadline_terminal_" + str(job["status"] in {"SUCCEEDED", "FAILED"}))
            time.sleep(5)
        proofs = []
        for _ in range(10):
            events = self.aws("logs", "get-log-events", {"logGroupName": self.preview["logGroupName"],
                "logStreamName": job.get("container", {}).get("logStreamName", ""), "startFromHead": True})["events"]
            for event in events:
                try:
                    row = json.loads(event["message"])
                except (ValueError, TypeError):
                    continue
                if row.get("schema") == "homework-preview-development-canary/v1":
                    proofs.append(row)
            if proofs:
                break
            time.sleep(3)
        if job["status"] != "SUCCEEDED" or len(proofs) != 1 or proofs[0].get("success") is not True:
            raise RuntimeError("real_codec_canary_failed:" + job_id)
        proof = proofs[0]
        required = ["development_database", "production_database_denied", "production_parameter_denied", "production_bucket_denied"]
        if (any(proof.get(k) is not True for k in required) or proof.get("release_id") != self.release
                or proof.get("source_codec") != "hevc" or proof.get("preview_codec") != "h264" or proof.get("remaining_objects") != 0):
            raise RuntimeError("real_codec_canary_incomplete")
        return {**runtime, "job_id": job_id, "status": job["status"], "proof": proof}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True)
    parser.add_argument("--release", required=True)
    parser.add_argument("--workers-version", required=True, type=int)
    parser.add_argument("--production-database", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    runtime = Runtime(json.load(sys.stdin), args.image, args.release, args.workers_version, args.apply)
    network = runtime.discover()
    result = {"mode": "plan", "network": network, "queue": runtime.preview["queueName"], "definition": runtime.name + ":1",
              "image": args.image, "release_id": args.release, "workers_parameter_version": args.workers_version}
    try:
        if args.apply:
            result = {"mode": "apply", **runtime.canary(runtime.converge(network), args.production_database), **runtime.evidence}
    except Exception as error:
        result.update(success=False, failure_type=type(error).__name__, **runtime.evidence)
        raise
    finally:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
