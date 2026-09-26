"""Trusted, closed-by-default runtime adapter for the bounded candidate QA lease.

AWS calls happen only when an owner invokes an observation. The fixed SSM
command reads Docker/process state; it never changes containers or queue data.
Domain cleanup and baseline queue exclusivity still require separately owned,
trusted evidence providers. No caller JSON or typed fixture is live evidence.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
import json
from pathlib import Path
import re
import time

from scripts.v1.candidate_qa_window import (
    InertReadback, Readback, RuntimeAdapter, WindowHold, binding, require,
)


ACCOUNT = "809466760795"
REGION = "ap-northeast-2"
PROFILE = f"arn:aws:iam::{ACCOUNT}:instance-profile/academy-api-qa"
TABLE = "academy-v1-video-job-lock"
LEASE_KEY = "__candidate_qa_window__"
LOCK_KEY = "__deployment_control_v2__"
CONTAINERS = {
    "api": ("academy-api", "academy-qa-api"),
    "ai": ("academy-ai-development", "academy-qa-ai-worker-cpu"),
    "tools": ("academy-tools-development", "academy-qa-tools-worker"),
    "messaging": ("academy-messaging-development", "academy-qa-messaging-worker"),
}
QUEUE_NAMES = {kind: f"academy-v1-development-{kind}-queue" for kind in ("ai", "tools", "messaging")}
QUEUE_ATTRS = (
    "ApproximateNumberOfMessages", "ApproximateNumberOfMessagesNotVisible",
    "ApproximateNumberOfMessagesDelayed",
)


@dataclass(frozen=True)
class QueueExclusivityEvidence:
    """Trusted controller proof; constructing this object is not live proof."""

    lease_id: str
    binding_sha256: str
    observed_at: int
    baseline_snapshot_sha256: str
    baseline_instance_id: str
    baseline_worker_pids: dict[str, tuple[int, ...]]


@dataclass(frozen=True)
class CleanupEvidence:
    """Owned fixture inventory across every named domain and disposable tenant."""

    lease_id: str
    binding_sha256: str
    observed_at: int
    tenant_ids: tuple[int, ...]
    resource_manifest_sha256: str
    residual_by_domain: dict[str, dict[str, int]]


@dataclass(frozen=True)
class BootstrapEvidence:
    """PREPARED runtime only; never an admission, cleanup or open-window proof."""

    binding_sha256: str
    observed_at: int
    instance_id: str
    lease_revision: int
    enforcement_expires_at: int
    container_names: tuple[str, ...]
    api_worker_pids: tuple[int, ...]


def _fresh(observed_at, now):
    return type(observed_at) is int and 0 <= now - observed_at <= 10


def _safe_count(value):
    return type(value) is int and value >= 0


class FixedSsmProbe:
    """Execute only the checked-in read-only source on one verified instance."""

    def __init__(self, ssm, *, sleeper=time.sleep):
        self.ssm = ssm
        self.sleeper = sleeper

    def read(self, instance_id):
        require(re.fullmatch(r"i-[0-9a-f]{17}", instance_id), "Invalid SSM QA target")
        source = Path(__file__).with_name("candidate_qa_probe.py").read_bytes()
        encoded = base64.b64encode(source).decode("ascii")
        command = (
            "python3 -c 'import base64;exec(base64.b64decode(\""
            + encoded + "\").decode(\"utf-8\"))'"
        )
        result = self.ssm.send_command(
            InstanceIds=[instance_id], DocumentName="AWS-RunShellScript",
            Parameters={"commands": [command]}, TimeoutSeconds=30,
        )
        command_id = result["Command"]["CommandId"]
        require(re.fullmatch(r"[0-9a-f-]{36}", command_id), "Invalid SSM probe command ID")
        for _ in range(8):
            try:
                invocation = self.ssm.get_command_invocation(CommandId=command_id, InstanceId=instance_id)
            except Exception as exc:
                # Run Command can briefly report this exact eventual-consistency
                # error after SendCommand. Auth/permission failures still HOLD.
                code = getattr(exc, "response", {}).get("Error", {}).get("Code")
                if code != "InvocationDoesNotExist":
                    raise
                self.sleeper(1)
                continue
            if invocation.get("Status") in {"Pending", "InProgress", "Delayed"}:
                self.sleeper(1)
                continue
            require(invocation.get("Status") == "Success" and invocation.get("ResponseCode") == 0
                    and not invocation.get("StandardErrorContent", "").strip(),
                    "Trusted QA probe did not succeed")
            output = invocation.get("StandardOutputContent", "")
            require(len(output) <= 120_000, "QA probe output exceeds bound")
            return json.loads(output)
        raise WindowHold("Trusted QA probe timed out")


class AWSRuntimeAdapter(RuntimeAdapter):
    """Observe committed lease, exact EC2/SSM target, Docker workers and queues.

    `cleanup_probe` and `queue_exclusivity_probe` must be owner-wired control
    functions, never request/body inputs. Until both exist, open/idle readback
    raises WindowHold. The adapter performs no cleanup or drain mutation.
    """

    def __init__(self, *, expected_slot_owner, ec2=None, ssm=None, sqs=None, ddb=None,
                 probe=None, cleanup_probe=None, queue_exclusivity_probe=None,
                 cleanup_domains=(), clock=time.time, sleeper=time.sleep):
        require(isinstance(expected_slot_owner, str) and bool(expected_slot_owner),
                "Exact QA slot owner required")
        self.expected_slot_owner = expected_slot_owner
        self._clients = {"ec2": ec2, "ssm": ssm, "sqs": sqs, "dynamodb": ddb}
        self._probe = probe
        self.cleanup_probe = cleanup_probe
        self.queue_exclusivity_probe = queue_exclusivity_probe
        self.cleanup_domains = frozenset(cleanup_domains)
        self.clock = clock
        self.sleeper = sleeper

    def _client(self, service):
        if self._clients[service] is None:
            import boto3
            from botocore.config import Config

            self._clients[service] = boto3.client(
                service, region_name=REGION,
                config=Config(connect_timeout=2, read_timeout=5, retries={"max_attempts": 1}),
            )
        return self._clients[service]

    def _target(self, record):
        endpoint = record.get("endpoint", "")
        require(re.fullmatch(r"ssm://i-[0-9a-f]{17}:8000", endpoint), "Exact QA endpoint required")
        instance_id = endpoint[len("ssm://"):].split(":", 1)[0]
        response = self._client("ec2").describe_instances(InstanceIds=[instance_id])
        require(not response.get("NextToken"), "Incomplete EC2 target inventory")
        instances = [item for reservation in response.get("Reservations", [])
                     for item in reservation.get("Instances", [])]
        require(len(instances) == 1, "Exact QA EC2 instance unavailable")
        instance = instances[0]
        tags = {item["Key"]: item["Value"] for item in instance.get("Tags", [])}
        run, attempt = record["lock_owner"].split(":")[1:]
        require(instance.get("InstanceId") == instance_id
                and instance.get("State", {}).get("Name") == "running"
                and record["profile"] == PROFILE
                and instance.get("IamInstanceProfile", {}).get("Arn") == PROFILE
                and tags.get("Name") == "academy-v1-api-development"
                and tags.get("Project") == "academy"
                and tags.get("ManagedBy") == "academy-api-development"
                and tags.get("QaMode") == "isolated-qa"
                and tags.get("SlotLeaseOwner") == self.expected_slot_owner == record["owner_task"]
                and tags.get("CandidateLeaseId") == record["lease_id"]
                and tags.get("CandidateSourceSha") == record["source_sha"]
                and tags.get("ReleaseId") == f"sha-{record['source_sha']}-run-{run}-{attempt}",
                "QA EC2 identity or ownership differs")
        groups = instance.get("SecurityGroups", [])
        require(len(groups) == 1 and groups[0].get("GroupName") == "academy-v1-api-development-sg",
                "QA EC2 security group inventory differs")
        group_id = groups[0].get("GroupId")
        interfaces = instance.get("NetworkInterfaces", [])
        require(len(interfaces) == 1 and interfaces[0].get("Groups") == groups,
                "QA EC2 network interface group differs")
        group_response = self._client("ec2").describe_security_groups(GroupIds=[group_id])
        require(not group_response.get("NextToken"), "Incomplete QA security group inventory")
        actual_groups = group_response.get("SecurityGroups", [])
        require(len(actual_groups) == 1 and actual_groups[0].get("GroupId") == group_id
                and actual_groups[0].get("GroupName") == "academy-v1-api-development-sg"
                and actual_groups[0].get("VpcId") == instance.get("VpcId")
                and actual_groups[0].get("IpPermissions") == [],
                "QA security group permits inbound traffic")
        info = self._client("ssm").describe_instance_information(
            Filters=[{"Key": "InstanceIds", "Values": [instance_id]}]
        )
        require(not info.get("NextToken"), "Incomplete SSM managed-instance inventory")
        managed = info.get("InstanceInformationList", [])
        require(len(managed) == 1 and managed[0].get("InstanceId") == instance_id
                and managed[0].get("PingStatus") == "Online"
                and managed[0].get("ResourceType") == "EC2Instance",
                "QA instance is not the exact online SSM target")
        return instance_id

    def _sessions(self, instance_id):
        result = self._client("ssm").describe_sessions(
            State="Active", Filters=[{"key": "Target", "value": instance_id}]
        )
        require(not result.get("NextToken"), "Incomplete active SSM session inventory")
        sessions = result.get("Sessions", [])
        require(all(item.get("Target") == instance_id and item.get("SessionId") for item in sessions),
                "SSM session target differs")
        return tuple("ssm:" + item["SessionId"] for item in sessions)

    def _probe_read(self, instance_id):
        source = self._probe or FixedSsmProbe(self._client("ssm")).read
        result = source(instance_id)
        require(isinstance(result, dict) and result.get("schema_version") == 1
                and _fresh(result.get("observed_at"), int(self.clock()))
                and result.get("required_env_exact") is True,
                "QA probe is stale or required launch marker differs")
        return result

    def _lock(self, record):
        item = self._client("dynamodb").get_item(
            TableName=TABLE, Key={"videoId": {"S": LOCK_KEY}}, ConsistentRead=True
        ).get("Item", {})
        require(item.get("owner", {}).get("S") == record["lock_owner"]
                and int(item.get("ttl", {}).get("N", "0")) > int(self.clock()),
                "Candidate shared lock is not owned")

    def _committed(self, record):
        response = self._client("dynamodb").transact_get_items(TransactItems=[
            {"Get": {"TableName": TABLE, "Key": {"videoId": {"S": LEASE_KEY}}}},
            {"Get": {"TableName": TABLE, "Key": {"videoId": {"S": LOCK_KEY}}}},
        ])
        rows = response.get("Responses", [])
        require(len(rows) == 2, "Committed QA lease and lock readback missing")
        lease, lock = rows[0].get("Item", {}), rows[1].get("Item", {})
        committed = json.loads(lease["record"]["S"])
        require(committed == record and lease["leaseId"]["S"] == record["lease_id"]
                and int(lease["revision"]["N"]) == record["revision"]
                and lock["owner"]["S"] == record["lock_owner"]
                and int(lock["ttl"]["N"]) > int(self.clock()),
                "QA lease revision or shared lock is not committed")

    def _queue_counts(self):
        counts = {}
        for kind, name in QUEUE_NAMES.items():
            url = self._client("sqs").get_queue_url(QueueName=name)["QueueUrl"]
            require(url == f"https://sqs.{REGION}.amazonaws.com/{ACCOUNT}/{name}",
                    "Exact development queue URL differs")
            attrs = self._client("sqs").get_queue_attributes(
                QueueUrl=url, AttributeNames=list(QUEUE_ATTRS)
            )["Attributes"]
            require(set(attrs) >= set(QUEUE_ATTRS), "Development queue inventory incomplete")
            values = [int(attrs[key]) for key in QUEUE_ATTRS]
            require(all(_safe_count(value) for value in values), "Development queue count invalid")
            counts[kind] = dict(zip(("visible", "inflight", "delayed"), values))
        return counts

    def _exclusivity(self, record):
        require(callable(self.queue_exclusivity_probe),
                "Trusted baseline queue exclusivity proof is not wired")
        proof = self.queue_exclusivity_probe(record)
        require(isinstance(proof, QueueExclusivityEvidence)
                and proof.lease_id == record["lease_id"]
                and proof.binding_sha256 == binding(record)
                and proof.baseline_snapshot_sha256 == record["baseline_sha256"]
                and re.fullmatch(r"i-[0-9a-f]{17}", proof.baseline_instance_id)
                and proof.baseline_instance_id != record["endpoint"][len("ssm://"):].split(":", 1)[0]
                and _fresh(proof.observed_at, int(self.clock()))
                and set(proof.baseline_worker_pids) == set(QUEUE_NAMES)
                and all(value == () for value in proof.baseline_worker_pids.values()),
                "Baseline worker exit/queue exclusivity proof differs")

    def _cleanup(self, record):
        require(callable(self.cleanup_probe) and self.cleanup_domains,
                "Trusted domain cleanup proof is not wired")
        proof = self.cleanup_probe(record)
        require(isinstance(proof, CleanupEvidence)
                and proof.lease_id == record["lease_id"]
                and proof.binding_sha256 == binding(record)
                and proof.tenant_ids == tuple(sorted(record["tenant_ids"]))
                and isinstance(proof.resource_manifest_sha256, str)
                and re.fullmatch(r"[0-9a-f]{64}", proof.resource_manifest_sha256)
                and _fresh(proof.observed_at, int(self.clock()))
                and set(proof.residual_by_domain) == self.cleanup_domains
                and all(set(value) >= {"db_rows", "r2_objects", "pending_outboxes"}
                        and all(_safe_count(count) and count == 0
                                      for count in value.values())
                        for value in proof.residual_by_domain.values()),
                "Domain cleanup-zero proof missing or nonzero")
        return True

    def observe_inert(self, record):
        try:
            self._lock(record)
            instance_id = self._target(record)
            sessions = self._sessions(instance_id)
            probe = self._probe_read(instance_id)
            require(probe.get("inert_ready") is True and probe.get("container_names") == []
                    and probe.get("containers") == {} and not sessions,
                    "Inert QA instance has containers or active sessions")
            return InertReadback(binding(record), int(self.clock()), instance_id,
                                 record["profile"], True, 0, 0)
        except WindowHold:
            raise
        except Exception:
            raise WindowHold("Trusted inert QA readback unavailable") from None

    def _inventory(self, record):
        self._committed(record)
        instance_id = self._target(record)
        sessions = self._sessions(instance_id)
        probe = self._probe_read(instance_id)
        require(set(probe.get("container_names", [])) == {item[0] for item in CONTAINERS.values()}
                and len(probe["container_names"]) == len(CONTAINERS)
                and set(probe.get("containers", {})) == set(CONTAINERS),
                "QA container inventory incomplete or foreign")
        require(probe.get("api_health") is True, "QA API port 8000 is not healthy on the exact host")
        api_inflight = []
        workers_inflight = {}
        admission = []
        for kind in CONTAINERS:
            active, identifiers = self._container(record, kind, probe["containers"][kind])
            admission.append(active)
            if kind == "api":
                api_inflight = ["api:" + item for item in identifiers]
            else:
                workers_inflight[kind] = tuple(identifiers)
        require(len(set(admission)) == 1, "QA processes disagree on admission state")
        return instance_id, sessions, probe, admission[0], api_inflight, workers_inflight

    def _observe(self, record, *, require_completion_proofs):
        try:
            _, sessions, _, active, api_inflight, workers_inflight = self._inventory(record)
            if require_completion_proofs:
                self._exclusivity(record)
            return Readback(
                binding_sha256=binding(record), observed_at=int(self.clock()),
                api_admission="lease-bound" if active else "closed",
                workers_receiving=active, workers_inflight=workers_inflight,
                queue_counts=self._queue_counts(),
                active_sessions=tuple(sessions) + tuple(api_inflight),
                enforcement_expires_at=record["expires_at"],
                cleanup_zero=self._cleanup(record) if require_completion_proofs else False,
                lease_revision=record["revision"],
            )
        except WindowHold:
            raise
        except Exception:
            raise WindowHold("Trusted candidate QA runtime readback unavailable") from None

    def observe_bootstrap(self, record):
        """Check PREPARED workers before pausing baseline queue consumers.

        This intentionally omits cleanup/exclusivity and returns a different
        type than Readback, so Window.open/admit cannot mistake it for proof.
        """
        require(record.get("state") == "prepared", "Bootstrap requires PREPARED lease")
        for attempt in range(4):
            try:
                instance_id, sessions, probe, active, api_inflight, workers = self._inventory(record)
                require(not sessions and not active and not api_inflight
                        and all(not identifiers for identifiers in workers.values()),
                        "Prepared QA runtime must be closed and idle")
                api_processes = probe["containers"]["api"]["inside"]["processes"]
                api_pids = tuple(sorted(item["pid"] for item in api_processes
                                        if item.get("ppid") == 1 and item.get("gunicorn") is True))
                return BootstrapEvidence(
                    binding(record), int(self.clock()), instance_id, record["revision"],
                    record["expires_at"], tuple(item[0] for item in CONTAINERS.values()), api_pids,
                )
            except WindowHold as exc:
                if str(exc) != "QA heartbeat revision pending" or attempt == 3:
                    raise
                self.sleeper(2)
            except Exception:
                raise WindowHold("Trusted PREPARED runtime bootstrap unavailable") from None
        raise WindowHold("QA heartbeat revision pending")

    def observe(self, record):
        return self._stable_observe(record, require_completion_proofs=True)

    def _stable_observe(self, record, *, require_completion_proofs):
        # A committed CAS can precede the process heartbeat by up to its 2s
        # interval. Re-read the same committed revision; never accept the old one.
        for attempt in range(4):
            try:
                return self._observe(record, require_completion_proofs=require_completion_proofs)
            except WindowHold as exc:
                if str(exc) != "QA heartbeat revision pending" or attempt == 3:
                    raise
                self.sleeper(2)
        raise WindowHold("QA heartbeat revision pending")

    def _container(self, record, kind, observed):
        name, repository = CONTAINERS[kind]
        digest = record["images"][kind]
        image_ref = f"{ACCOUNT}.dkr.ecr.{REGION}.amazonaws.com/{repository}@{digest}"
        require(observed.get("name") == name and observed.get("status") == "running"
                and observed.get("health") == "healthy"
                and observed.get("image_ref") == image_ref
                and observed.get("image_id") == observed.get("actual_image_id")
                and observed.get("cmd") == observed.get("image_cmd")
                and observed.get("entrypoint") == observed.get("image_entrypoint")
                and image_ref in observed.get("repo_digests", []),
                "QA container immutable image differs")
        require(observed.get("network_mode") == "host", "QA container network mode differs")
        ports = observed.get("ports")
        require(isinstance(ports, dict), "QA published-port inventory missing")
        published = {port: bindings for port, bindings in ports.items() if bindings}
        require(not published, "Host-network QA container unexpectedly publishes a Docker port")
        env = observed.get("env", {})
        require(env.get("ACADEMY_RUNTIME_ENV") == "development"
                and env.get("ACADEMY_QA_MODE") == "isolated-qa"
                and env.get("CANDIDATE_LEASE_REQUIRED") == "true"
                and env.get("ACADEMY_QA_LEASE_ID") == record["lease_id"]
                and env.get("ACADEMY_QA_BINDING_SHA256") == binding(record)
                and env.get("ACADEMY_QA_ACTIVITY_DIR", "/tmp/academy-qa-activity") == "/tmp/academy-qa-activity"
                and env.get("ACADEMY_QA_MESSAGE_KEY_VERSION") == str(record["message_key_version"]),
                "QA container environment identity differs")
        if kind == "ai":
            require(all(env.get(f"AI_SQS_QUEUE_NAME_{tier}") == QUEUE_NAMES["ai"]
                        for tier in ("LITE", "BASIC", "PREMIUM")), "QA AI queue differs")
        elif kind == "tools":
            require(env.get("TOOLS_SQS_QUEUE_NAME") == QUEUE_NAMES["tools"], "QA Tools queue differs")
        elif kind == "messaging":
            require(env.get("MESSAGING_SQS_QUEUE_NAME") == QUEUE_NAMES["messaging"]
                    and env.get("SOLAPI_MOCK") == "true", "QA messaging boundary differs")
        inside = observed.get("inside", {})
        now = int(self.clock())
        require(_fresh(inside.get("observed_at"), now), "QA process inventory stale")
        processes = inside.get("processes", [])
        require(isinstance(processes, list), "QA process inventory malformed")
        if kind == "api":
            expected = int(env.get("GUNICORN_WORKERS", "4"))
            require(1 <= expected <= 32, "QA Gunicorn worker count invalid")
            require(any(item.get("pid") == 1 and item.get("gunicorn") is True
                        for item in processes), "QA Gunicorn master missing")
            pids = {item["pid"] for item in processes if item.get("ppid") == 1
                    and item.get("gunicorn") is True and item.get("pid") != 1}
            require(len(pids) == expected, "QA Gunicorn worker inventory differs")
        else:
            pids = {1}
            require(any(item.get("pid") == 1 and item.get("command") == kind
                        for item in processes), "QA worker process missing")
        starts = {item.get("pid"): item.get("started_at") for item in processes if item.get("pid") in pids}
        require(set(starts) == pids and all(type(value) is int and value <= now for value in starts.values()),
                "QA process start inventory incomplete")
        snapshots = inside.get("snapshots", [])
        require(isinstance(snapshots, list) and len(snapshots) == len(pids),
                "QA process heartbeat count differs")
        seen = set()
        active = set()
        identifiers = []
        for item in snapshots:
            snap = item.get("data", {})
            pid = snap.get("pid")
            process_id = snap.get("process_id")
            if snap.get("revision") != record["revision"]:
                raise WindowHold("QA heartbeat revision pending")
            require(type(pid) is int and pid in pids and pid not in seen
                    and isinstance(process_id, str) and re.fullmatch(r"[0-9a-f]{32}", process_id)
                    and item.get("filename") == f"{kind}-{pid}-{process_id}.json"
                    and _fresh(item.get("mtime"), now)
                    and _fresh(snap.get("observed_at"), now)
                    and item["mtime"] >= starts[pid]
                    and snap["observed_at"] >= starts[pid]
                    and snap.get("enabled") is True and snap.get("alive") is True
                    and snap.get("kind") == kind and snap.get("lease_verified") is True
                    and snap.get("control_hold") is False and snap.get("holds") == []
                    and snap.get("lease_id") == record["lease_id"]
                    and snap.get("binding_sha256") == binding(record)
                    and snap.get("enforcement_expires_at") == record["expires_at"]
                    and snap.get("admission") in {"open", "closed"},
                    "QA heartbeat missing, replayed, held or bound to another revision")
            seen.add(pid)
            active.add(snap["admission"] == "open")
            inflight = snap.get("inflight")
            require(isinstance(inflight, list), "QA in-flight list missing")
            for operation in inflight:
                hashed = operation.get("operation_sha256")
                require(isinstance(hashed, str) and re.fullmatch(r"[0-9a-f]{64}", hashed)
                        and operation.get("lease_revision") == record["revision"],
                        "QA in-flight identity or revision differs")
                message_id = operation.get("message_id")
                if kind != "api":
                    require(isinstance(message_id, str) and re.fullmatch(r"[0-9a-f]{32}", message_id),
                            "QA worker message identity missing")
                identifiers.append(f"{pid}:{message_id or hashed}")
        require(seen == pids and len(active) == 1, "QA process admission inventory incomplete")
        return active.pop(), identifiers

    def drain(self, record):
        """Observe drain only; never stop/ACK/delete active work."""
        require(record.get("state") == "draining", "Drain needs committed draining state")
        proof = self._stable_observe(record, require_completion_proofs=False)
        require(proof.api_admission == "closed" and proof.workers_receiving is False,
                "QA admission is not closed")
        return proof
