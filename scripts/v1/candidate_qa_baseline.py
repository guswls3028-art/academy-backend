"""Cooperative baseline consumer exclusion and exact-container restoration.

This is a controller building block, not a workflow entry point. It never
terminates capacity or removes containers. A failed or uncertain step retains
the root-only host receipt and requires the release owner to resolve HOLD.
"""
from __future__ import annotations

import base64
import json
import re
import time
from pathlib import Path

from scripts.v1.candidate_qa_launch import CONTAINERS, InertLauncher, image_uris
from scripts.v1.candidate_qa_window import Readback, WindowHold, binding, require, verify_readback
from scripts.v1.candidate_slot import ACCOUNT, REGION, baseline_matches, load_snapshot, recovery_inventory, tags

WORKERS = ("ai", "tools", "messaging")
IMAGE_FIELDS = {"ai": "AiImageUri", "tools": "ToolsImageUri", "messaging": "MessagingImageUri"}
QUEUE_NAMES = {kind: f"academy-v1-development-{kind}-queue" for kind in WORKERS}
QUEUE_ATTRIBUTES = (
    "ApproximateNumberOfMessages", "ApproximateNumberOfMessagesNotVisible",
    "ApproximateNumberOfMessagesDelayed",
)


# Fixed trusted-controller program. The encoded plan contains only instance,
# lease, image and queue identities; Docker environment values never leave host.
HOST_PROGRAM = r'''
import base64, json, os, re, subprocess, sys, time, urllib.request
from pathlib import Path

def run(args):
    result = subprocess.run(args, text=True, capture_output=True, timeout=30)
    if result.returncode:
        raise RuntimeError("baseline host command failed")
    return result.stdout.strip()

def inspect(name):
    rows = json.loads(run(["docker", "inspect", name]))
    if len(rows) != 1:
        raise RuntimeError("baseline container inventory differs")
    return rows[0]

def policy(item):
    raw = item["HostConfig"]["RestartPolicy"]
    name = raw["Name"]
    count = raw.get("MaximumRetryCount", 0)
    if name not in ("no", "always", "unless-stopped", "on-failure") or type(count) is not int or count < 0:
        raise RuntimeError("baseline restart policy differs")
    return {"name": name, "count": count}

def env_values(item):
    values = {}
    for entry in item["Config"]["Env"]:
        key, sep, value = entry.partition("=")
        if not sep or key in values:
            raise RuntimeError("baseline container environment malformed")
        values[key] = value
    return values

def env_ok(item, kind, plan):
    values = env_values(item)
    expected = {"ACADEMY_RUNTIME_ENV": "development",
                "ACADEMY_DEVELOPMENT_RELEASE_ID": plan["release_id"],
                "DJANGO_SETTINGS_MODULE": "apps.api.config.settings.worker",
                "DB_NAME": "academy_api_development",
                "DB_USER": "academy_api_development_app",
                "TOOLS_SQS_QUEUE_NAME": "academy-v1-development-tools-queue",
                "MESSAGING_SQS_QUEUE_NAME": "academy-v1-development-messaging-queue"}
    expected.update({"AI_SQS_QUEUE_NAME_" + tier: "academy-v1-development-ai-queue"
                     for tier in ("LITE", "BASIC", "PREMIUM")})
    return (all(values.get(key) == value for key, value in expected.items())
            and values.get("ACADEMY_QA_MODE", "") == ""
            and values.get("CANDIDATE_LEASE_REQUIRED", "").lower() in ("", "0", "false")
            and values.get("SOLAPI_MOCK", "").lower() == "true"
            and values.get("VIDEO_BATCH_JOB_QUEUE", "") == ""
            and values.get("VIDEO_BATCH_JOB_DEFINITION", "") == ""
            and (kind != "ai" or (values.get("AI_WORKER_IDLE_SCALE_IN_ENABLED") == "0"
                                  and values.get("EC2_IDLE_STOP_THRESHOLD") == "0")))

def workers(plan):
    found = {}
    commands = {"ai": ["python", "-m", "apps.worker.ai_worker.sqs_main_cpu"],
                "tools": ["python", "-m", "apps.worker.tools_worker.sqs_main"],
                "messaging": ["python", "-m", "apps.worker.messaging_worker.sqs_main"]}
    for kind, name in plan["containers"].items():
        item = inspect(name)
        state = item["State"]
        if (item["Name"] != "/" + name or item["Config"]["Image"] != plan["images"][kind]
                or item["Config"]["Cmd"] != commands[kind]
                or item["HostConfig"]["NetworkMode"] != "host"
                or not env_ok(item, kind, plan)):
            raise RuntimeError("baseline worker identity or environment differs")
        health = state.get("Health", {}).get("Status")
        if state["Running"] and health is not None and health not in ("healthy", "starting"):
            raise RuntimeError("baseline worker health differs")
        found[kind] = {"id": item["Id"], "image": item["Config"]["Image"],
                       "running": state["Running"], "pid": state["Pid"],
                       "policy": policy(item), "health": health}
    return found

def api_ok(plan):
    item = inspect("academy-api")
    values = env_values(item)
    if (item["Name"] != "/academy-api" or item["Config"]["Image"] != plan["api_image"]
            or item["HostConfig"]["NetworkMode"] != "host"
            or not item["State"]["Running"] or item["State"]["Pid"] <= 0
            or values.get("ACADEMY_RUNTIME_ENV") != "development"
            or values.get("ACADEMY_DEVELOPMENT_RELEASE_ID") != plan["release_id"]
            or values.get("DJANGO_SETTINGS_MODULE") != "apps.api.config.settings.development"
            or values.get("DB_NAME") != "academy_api_development"
            or values.get("DB_USER") != "academy_api_development_app"
            or values.get("ACADEMY_QA_MODE", "") != ""
            or values.get("CANDIDATE_LEASE_REQUIRED", "").lower() not in ("", "0", "false")
            or values.get("SOLAPI_MOCK", "").lower() != "true"):
        return False
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for path in ("healthz", "health"):
        response = opener.open("http://127.0.0.1:8000/" + path, timeout=5)
        if response.status != 200:
            return False
    return True

def receipt_path(plan):
    directory = Path("/opt/academy-qa/baseline-consumers")
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if directory.stat().st_mode & 0o077 or directory.stat().st_uid != 0:
        raise RuntimeError("baseline receipt directory is not root-only")
    return directory / (plan["lease_id"] + ".json")

def receipt(plan, current, create=False):
    path = receipt_path(plan)
    if not path.exists() and create:
        if not all(item["running"] and item["pid"] > 0 for item in current.values()):
            raise RuntimeError("baseline worker already absent without recovery receipt")
        data = {"lease_id": plan["lease_id"], "binding_sha256": plan["binding_sha256"],
                "baseline_snapshot_sha256": plan["baseline_snapshot_sha256"],
                "baseline_instance_id": plan["instance_id"],
                "workers": {kind: {"id": item["id"], "image": item["image"],
                                   "policy": item["policy"]} for kind, item in current.items()}}
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as handle:
            json.dump(data, handle, sort_keys=True)
            handle.flush(); os.fsync(handle.fileno())
    if not path.exists():
        raise RuntimeError("baseline pause receipt missing")
    if path.stat().st_mode & 0o077 or path.stat().st_uid != 0:
        raise RuntimeError("baseline pause receipt is not root-only")
    data = json.loads(path.read_text())
    if (data.get("lease_id") != plan["lease_id"]
            or data.get("binding_sha256") != plan["binding_sha256"]
            or data.get("baseline_snapshot_sha256") != plan["baseline_snapshot_sha256"]
            or data.get("baseline_instance_id") != plan["instance_id"]
            or set(data.get("workers", {})) != set(current)):
        raise RuntimeError("baseline pause receipt belongs to another lease")
    for kind, item in current.items():
        saved = data["workers"][kind]
        if saved["id"] != item["id"] or saved["image"] != item["image"]:
            raise RuntimeError("baseline container was replaced")
        if (saved["policy"]["name"] not in ("no", "always", "unless-stopped", "on-failure")
                or type(saved["policy"].get("count")) is not int or saved["policy"]["count"] < 0):
            raise RuntimeError("baseline original policy invalid")
    return data

def summary(plan, current, has_receipt):
    original = receipt(plan, current)["workers"] if has_receipt else {}
    return {"status": "READBACK", "api_healthy": api_ok(plan), "receipt": has_receipt,
            "workers": current,
            "original_policies": {kind: item["policy"] for kind, item in original.items()}}

def main():
    if os.geteuid() != 0:
        raise RuntimeError("root-owned baseline receipt required")
    plan = json.loads(base64.b64decode(sys.argv[1], validate=True))
    mode = sys.argv[2]
    if mode not in ("inspect", "pause", "resume") or not re.fullmatch(r"[0-9a-f]{32}", plan["lease_id"]):
        raise RuntimeError("baseline command identity invalid")
    current = workers(plan)
    if not api_ok(plan):
        raise RuntimeError("baseline API health differs")
    if mode == "pause":
        receipt(plan, current, create=True)  # Durable before any Docker policy update.
        for kind, item in current.items():
            if item["running"]:
                run(["docker", "update", "--restart=no", item["id"]])
                # Inside the exact worker container, signal PID 1 cooperatively.
                # No forced shutdown, removal or instance termination.
                run(["docker", "exec", item["id"], "python", "-c",
                     "import os,signal;os.kill(1,signal.SIGTERM)"])
        deadline = time.monotonic() + 2400
        while time.monotonic() < deadline:
            current = workers(plan)
            if all(not item["running"] and item["pid"] == 0 and
                   item["policy"]["name"] == "no" for item in current.values()):
                break
            time.sleep(5)
        else:
            raise RuntimeError("baseline worker drain timed out; retain receipt and HOLD")
    elif mode == "resume":
        saved = receipt(plan, current)
        for kind, item in current.items():
            original = saved["workers"][kind]["policy"]
            if item["running"] or item["pid"] != 0 or item["policy"]["name"] != "no":
                raise RuntimeError("baseline worker is not in paused state")
            restart = original["name"]
            if restart == "on-failure":
                restart += ":" + str(original["count"])
            run(["docker", "update", "--restart=" + restart, item["id"]])
            run(["docker", "start", item["id"]])
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            current = workers(plan)
            if all(item["running"] and item["pid"] > 0 and
                   item["policy"] == saved["workers"][kind]["policy"] and
                   item["health"] == "healthy" for kind, item in current.items()):
                break
            time.sleep(3)
        else:
            raise RuntimeError("baseline worker health did not recover; HOLD")
    else:
        path = receipt_path(plan)
        if path.exists():
            receipt(plan, current)
    print(json.dumps(summary(plan, current, receipt_path(plan).exists()), sort_keys=True))

try:
    main()
except Exception:
    # No Docker output, environment value, receipt content or credential in SSM.
    print('{"status":"BASELINE_HOLD"}')
    raise SystemExit(2)
'''


class SSMBaselineCommands:
    def __init__(self, ssm, sleeper=time.sleep):
        self.ssm, self.sleeper = ssm, sleeper

    def run(self, instance_id, mode, plan):
        require(mode in {"inspect", "pause", "resume"}, "Unknown baseline command")
        encoded = base64.b64encode(json.dumps(plan, sort_keys=True).encode()).decode()
        command = f"python3 - '{encoded}' '{mode}' <<'ACADEMY_BASELINE_CONSUMERS'\n{HOST_PROGRAM}\nACADEMY_BASELINE_CONSUMERS"
        try:
            result = self.ssm.send_command(
                InstanceIds=[instance_id], DocumentName="AWS-RunShellScript",
                Parameters={"commands": [command], "executionTimeout": ["2550"]},
                TimeoutSeconds=2550, Comment=f"Candidate baseline consumers {mode}",
            )
            command_id = result["Command"]["CommandId"]
        except Exception:
            raise WindowHold("Baseline SSM submission uncertain; retain receipt and HOLD") from None
        require(re.fullmatch(r"[0-9a-f-]{36}", command_id), "SSM command acknowledgement differs")
        for _ in range(510):
            try:
                observed = self.ssm.get_command_invocation(CommandId=command_id, InstanceId=instance_id)
            except Exception as exc:
                if getattr(exc, "response", {}).get("Error", {}).get("Code") == "InvocationDoesNotExist":
                    self.sleeper(5)
                    continue
                raise WindowHold("Baseline SSM readback unavailable") from None
            status = observed.get("Status")
            if status == "Success":
                require(observed.get("ResponseCode") == 0,
                        "Baseline SSM process did not exit cleanly")
                try:
                    output = json.loads(observed["StandardOutputContent"])
                except (KeyError, ValueError, TypeError):
                    raise WindowHold("Baseline SSM result malformed") from None
                require(output.get("status") == "READBACK", "Baseline host did not confirm readback")
                return output
            require(status in {"Pending", "InProgress", "Delayed"}, "Baseline SSM command failed; HOLD")
            self.sleeper(5)
        raise WindowHold("Baseline SSM command timed out; retain receipt and HOLD")


class BaselineConsumers:
    def __init__(self, *, ec2, ssm, sqs, sts, store, runtime, clock=time.time, commands=None):
        self.ec2, self.ssm, self.sqs = ec2, ssm, sqs
        self.store, self.runtime, self.clock = store, runtime, clock
        self.authority = InertLauncher(ec2=ec2, ssm=ssm, sts=sts, store=store, clock=clock)
        self.commands = commands or SSMBaselineCommands(ssm)

    def _owned(self, record, snapshot_path, snapshot_sha256, state=None):
        if state is None:
            require(record.get("state") in {"prepared", "active", "draining", "closed"},
                    "Baseline readback has no committed lease state")
        else:
            require(record.get("state") == state and not record.get("control_hold"),
                    "Baseline transition is not in its authorized lease state")
        require(record.get("baseline_sha256") == snapshot_sha256 and
                re.fullmatch(r"[0-9a-f]{64}", snapshot_sha256), "Baseline digest differs")
        snapshot = load_snapshot(Path(snapshot_path), snapshot_sha256)
        require(snapshot["owner"] == record["owner_task"] and snapshot["lock_owner"] == record["lock_owner"],
                "Baseline owner or shared lock differs")
        require(re.fullmatch(r"i-[0-9a-f]{17}", snapshot.get("instance_id", ""))
                and re.fullmatch(r"ssm://i-[0-9a-f]{17}:8000", record.get("endpoint", ""))
                and snapshot["instance_id"] != record["endpoint"].split("//", 1)[1].split(":", 1)[0],
                "Exact distinct baseline and QA instances required")
        self.authority._authority(record["lock_owner"])
        require(self.store.read() == record, "Committed QA lease changed")
        return snapshot

    def _inventory(self, record, snapshot):
        result = self.ec2.describe_instances(Filters=[
            {"Name": "tag:Name", "Values": ["academy-v1-api-development"]},
            {"Name": "tag:ManagedBy", "Values": ["academy-api-development"]},
            {"Name": "instance-state-name", "Values": ["pending", "running", "stopping", "stopped"]},
        ])
        require(not result.get("NextToken"), "Baseline instance inventory incomplete")
        instances = [i for reservation in result.get("Reservations", [])
                     for i in reservation.get("Instances", [])]
        recovery_inventory(snapshot, instances)
        qa_instance = record["endpoint"].split("//", 1)[1].split(":", 1)[0]
        require({i["InstanceId"] for i in instances} == {snapshot["instance_id"], qa_instance},
                "Baseline or exact QA instance is missing or foreign")
        baseline = next(i for i in instances if i["InstanceId"] == snapshot["instance_id"])
        require(baseline["State"]["Name"] == "running" and baseline_matches(snapshot, baseline),
                "Baseline identity or image/environment tags changed")
        qa = next(i for i in instances if i["InstanceId"] == qa_instance)
        qa_tags = tags(qa)
        release_id = "sha-" + record["source_sha"] + "-run-" + record["lock_owner"].split(":", 1)[1].replace(":", "-")
        expected = {"QaMode": "isolated-qa", "SlotLeaseOwner": record["owner_task"],
                    "CandidateLeaseId": record["lease_id"],
                    "CandidateBaselineSha256": record["baseline_sha256"],
                    "CandidateSourceSha": record["source_sha"], "ReleaseId": release_id}
        uris = image_uris(record["images"])
        expected.update({field: uris[kind] for field, kind in (
            ("ApiImageUri", "api"), ("AiImageUri", "ai"),
            ("ToolsImageUri", "tools"), ("MessagingImageUri", "messaging"),
        )})
        require(qa["State"]["Name"] == "running" and
                qa.get("IamInstanceProfile", {}).get("Arn") == record["profile"] and
                all(qa_tags.get(key) == value for key, value in expected.items()),
                "Prepared QA instance ownership or immutable image tags differ")
        return baseline

    def _sessions_zero(self, instance_id):
        result = self.ssm.describe_sessions(State="Active", Filters=[{"key": "Target", "value": instance_id}])
        require(not result.get("Sessions") and not result.get("NextToken"),
                "Active baseline SSM session owns the instance")

    def _queues(self, *, zero_required):
        for kind, name in QUEUE_NAMES.items():
            url = self.sqs.get_queue_url(QueueName=name)["QueueUrl"]
            require(url == f"https://sqs.{REGION}.amazonaws.com/{ACCOUNT}/{name}",
                    "Development queue identity differs")
            result = self.sqs.get_queue_attributes(QueueUrl=url, AttributeNames=list(QUEUE_ATTRIBUTES))
            values = result.get("Attributes", {})
            require(set(values) == set(QUEUE_ATTRIBUTES) and
                    all(isinstance(values[key], str)
                        and re.fullmatch(r"[0-9]+", values[key])
                        for key in QUEUE_ATTRIBUTES),
                    f"{kind} queue inventory malformed")
            require(not zero_required or all(int(values[key]) == 0 for key in QUEUE_ATTRIBUTES),
                    f"{kind} queue is not freshly empty")

    @staticmethod
    def _plan(record, snapshot, digest):
        return {"lease_id": record["lease_id"], "binding_sha256": binding(record),
                "baseline_snapshot_sha256": digest, "instance_id": snapshot["instance_id"],
                "release_id": snapshot["release"], "api_image": snapshot["images"]["ApiImageUri"],
                "containers": {kind: CONTAINERS[kind] for kind in WORKERS},
                "images": {kind: snapshot["images"][IMAGE_FIELDS[kind]] for kind in WORKERS}}

    @staticmethod
    def _host_proof(proof, *, paused, has_receipt):
        require(proof.get("status") == "READBACK" and proof.get("api_healthy") is True
                and proof.get("receipt") is has_receipt
                and set(proof.get("workers", {})) == set(WORKERS),
                "Baseline host readback incomplete")
        if has_receipt:
            require(set(proof.get("original_policies", {})) == set(WORKERS),
                    "Baseline original restart policies unavailable")
        for kind, item in proof["workers"].items():
            require(type(item.get("running")) is bool and type(item.get("pid")) is int,
                    "Baseline worker PID malformed")
            if paused:
                require(item["running"] is False and item["pid"] == 0
                        and item.get("policy", {}).get("name") == "no",
                        "Baseline worker still receives or may restart")
            else:
                require(item["running"] is True and item["pid"] > 0
                        and item.get("health") == "healthy"
                        and (not has_receipt or
                             item.get("policy") == proof["original_policies"][kind]),
                        "Baseline worker health not restored")

    def _bootstrap(self, record):
        try:
            from scripts.v1.candidate_qa_observer import AWSRuntimeAdapter, BootstrapEvidence
        except ImportError:
            raise WindowHold("Trusted QA bootstrap observer is not installed") from None
        require(isinstance(self.runtime, AWSRuntimeAdapter),
                "Trusted QA bootstrap observer is not installed")
        observe = getattr(self.runtime, "observe_bootstrap", None)
        require(callable(observe), "Trusted QA bootstrap observer is not installed")
        proof = observe(record)
        now = int(self.clock())
        require(isinstance(proof, BootstrapEvidence)
                and proof.binding_sha256 == binding(record)
                and 0 <= now - proof.observed_at <= 10
                and proof.instance_id == record["endpoint"].split("//", 1)[1].split(":", 1)[0]
                and proof.lease_revision == record["revision"]
                and proof.enforcement_expires_at == record["expires_at"]
                and set(proof.container_names) == set(CONTAINERS.values())
                and len(proof.container_names) == len(CONTAINERS)
                and bool(proof.api_worker_pids)
                and len(set(proof.api_worker_pids)) == len(proof.api_worker_pids)
                and all(type(pid) is int and pid > 0 for pid in proof.api_worker_pids),
                "QA PREPARED bootstrap proof incomplete")

    def pause(self, record, *, snapshot_path, snapshot_sha256):
        snapshot = self._owned(record, snapshot_path, snapshot_sha256, "prepared")
        require(type(record.get("expires_at")) is int
                and int(self.clock()) < record["expires_at"] - 30,
                "Prepared QA lease is closing; baseline stays running")
        self._bootstrap(record)  # No baseline mutation before actual QA boot proof.
        self._inventory(record, snapshot)
        self._sessions_zero(snapshot["instance_id"])
        plan = self._plan(record, snapshot, snapshot_sha256)
        self._host_proof(self.commands.run(snapshot["instance_id"], "inspect", plan),
                         paused=False, has_receipt=False)
        self.authority._authority(record["lock_owner"])
        require(self.store.read() == record and int(self.clock()) < record["expires_at"] - 30,
                "Prepared lease changed or expired before baseline pause")
        self.commands.run(snapshot["instance_id"], "pause", plan)
        # A separate post-command observation is required; the command's own
        # success alone is not proof that workers stayed down or queues drained.
        return self.observe_exclusivity(
            record, snapshot_path=snapshot_path, snapshot_sha256=snapshot_sha256,
        )

    def observe_exclusivity(self, record, *, snapshot_path, snapshot_sha256):
        """Fresh trusted probe for each QA observe; never cache the pause proof."""
        try:
            from scripts.v1.candidate_qa_observer import QueueExclusivityEvidence
        except ImportError:
            raise WindowHold("Trusted QA exclusivity evidence type is not installed") from None
        snapshot = self._owned(record, snapshot_path, snapshot_sha256)
        plan = self._plan(record, snapshot, snapshot_sha256)
        started = int(self.clock())
        self._inventory(record, snapshot)
        self._sessions_zero(snapshot["instance_id"])
        self._host_proof(self.commands.run(snapshot["instance_id"], "inspect", plan),
                         paused=True, has_receipt=True)
        self._queues(zero_required=record["state"] in {"prepared", "closed"})
        now = int(self.clock())
        require(0 <= now - started <= 10 and self.store.read() == record,
                "Queue exclusivity readback stale or lease changed")
        return QueueExclusivityEvidence(
            lease_id=record["lease_id"], binding_sha256=binding(record), observed_at=now,
            baseline_snapshot_sha256=snapshot_sha256, baseline_instance_id=snapshot["instance_id"],
            baseline_worker_pids={kind: () for kind in WORKERS},
        )

    def resume(self, record, *, snapshot_path, snapshot_sha256):
        snapshot = self._owned(record, snapshot_path, snapshot_sha256, "closed")
        require(record.get("restore_ready") is True, "QA completion and cleanup are not reconciled")
        try:
            from scripts.v1.candidate_qa_observer import AWSRuntimeAdapter
        except ImportError:
            raise WindowHold("Trusted QA idle observer is not installed") from None
        require(isinstance(self.runtime, AWSRuntimeAdapter),
                "Trusted QA idle observer is not installed")
        proof = self.runtime.observe(record)
        require(isinstance(proof, Readback), "Trusted QA idle proof unavailable")
        verify_readback(record, proof, int(self.clock()), idle=True, drained=True)
        self._inventory(record, snapshot)
        self._sessions_zero(snapshot["instance_id"])
        self._queues(zero_required=True)
        plan = self._plan(record, snapshot, snapshot_sha256)
        self._host_proof(self.commands.run(snapshot["instance_id"], "inspect", plan),
                         paused=True, has_receipt=True)
        self.authority._authority(record["lock_owner"])
        require(self.store.read() == record, "Closed lease changed before baseline resume")
        self.commands.run(snapshot["instance_id"], "resume", plan)
        self._inventory(record, snapshot)
        self._host_proof(self.commands.run(snapshot["instance_id"], "inspect", plan),
                         paused=False, has_receipt=True)
        require(self.store.read() == record, "Closed lease changed during baseline resume")
