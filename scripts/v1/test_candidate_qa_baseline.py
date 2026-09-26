"""Offline state/ownership tests for cooperative baseline worker exclusion."""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import types
import unittest
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import candidate_qa_baseline as baseline
from scripts.v1.candidate_qa_window import Readback, WindowHold, binding

OWNER = "01a0d04a-d64f-7473-9f58-61a8e983dcc0"
LEASE_ID = "a" * 32
SOURCE = "b" * 40
BASELINE_ID = "i-" + "1" * 17
QA_ID = "i-" + "2" * 17
RELEASE = f"sha-{SOURCE}-run-123-1"
REGISTRY = f"{baseline.ACCOUNT}.dkr.ecr.{baseline.REGION}.amazonaws.com"
IMAGE_ROLES = {"ApiImageUri": "api", "ToolsImageUri": "tools-worker",
               "AiImageUri": "ai-worker-cpu", "MessagingImageUri": "messaging-worker"}


@dataclass(frozen=True)
class BootstrapEvidence:
    binding_sha256: str
    observed_at: int
    instance_id: str
    lease_revision: int
    enforcement_expires_at: int
    container_names: tuple[str, ...]
    api_worker_pids: tuple[int, ...]


@dataclass(frozen=True)
class QueueExclusivityEvidence:
    lease_id: str
    binding_sha256: str
    observed_at: int
    baseline_snapshot_sha256: str
    baseline_instance_id: str
    baseline_worker_pids: dict[str, tuple[int, ...]]


class Store:
    table = "academy-v1-video-job-lock"

    def __init__(self, record):
        self.record = record
        self.client = self

    def read(self):
        return self.record

    def get_item(self, **kwargs):
        return {"Item": {"owner": {"S": "candidate:123:1"}, "ttl": {"N": "2000"}}}


class EC2:
    def __init__(self, snapshot, record):
        qa_uris = baseline.image_uris(record["images"])
        self.instances = [
            {"InstanceId": BASELINE_ID, "State": {"Name": "running"},
             "ImageId": snapshot["ami"], "InstanceType": snapshot["instance_type"],
             "IamInstanceProfile": {"Arn": snapshot["profile"]},
             "Tags": [{"Key": key, "Value": value} for key, value in {
                 "ReleaseId": RELEASE, "ApiEnvVersion": "3", "WorkersEnvVersion": "4",
                 **snapshot["images"],
             }.items()]},
            {"InstanceId": QA_ID, "State": {"Name": "running"},
             "IamInstanceProfile": {"Arn": record["profile"]},
             "Tags": [{"Key": key, "Value": value} for key, value in {
                 "SlotLeaseOwner": OWNER, "QaMode": "isolated-qa",
                 "ReleaseId": RELEASE, "CandidateLeaseId": LEASE_ID,
                 "CandidateBaselineSha256": record["baseline_sha256"],
                 "CandidateSourceSha": SOURCE,
                 "ApiImageUri": qa_uris["api"], "AiImageUri": qa_uris["ai"],
                 "ToolsImageUri": qa_uris["tools"],
                 "MessagingImageUri": qa_uris["messaging"],
             }.items()]},
        ]

    def describe_instances(self, **kwargs):
        return {"Reservations": [{"Instances": self.instances}]}


class SSM:
    sessions = ()

    def describe_sessions(self, **kwargs):
        return {"Sessions": list(self.sessions)}


class SQS:
    def __init__(self):
        self.counts = {kind: {key: "0" for key in baseline.QUEUE_ATTRIBUTES}
                       for kind in baseline.WORKERS}

    def get_queue_url(self, *, QueueName):
        return {"QueueUrl": f"https://sqs.{baseline.REGION}.amazonaws.com/{baseline.ACCOUNT}/{QueueName}"}

    def get_queue_attributes(self, *, QueueUrl, AttributeNames):
        kind = next(kind for kind, name in baseline.QUEUE_NAMES.items() if QueueUrl.endswith(name))
        return {"Attributes": self.counts[kind]}


class Runtime:
    def __init__(self, record):
        self.record = record
        self.bootstrap_calls = 0
        self.idle_calls = 0
        self.idle_override = None
        self.queue_exclusivity_probe = None

    def observe_bootstrap(self, record):
        self.bootstrap_calls += 1
        return BootstrapEvidence(
            binding(record), 1000, QA_ID, record["revision"], record["expires_at"],
            tuple(baseline.CONTAINERS.values()), (101, 102, 103, 104),
        )

    def observe(self, record):
        self.idle_calls += 1
        if self.queue_exclusivity_probe is not None:
            self.queue_exclusivity_probe(record)
        return Readback(
            binding_sha256=binding(record), observed_at=1000, api_admission="closed",
            workers_receiving=False, workers_inflight={kind: () for kind in baseline.WORKERS},
            queue_counts={kind: {"visible": 0, "inflight": 0, "delayed": 0}
                          for kind in baseline.WORKERS},
            active_sessions=(), enforcement_expires_at=record["expires_at"],
            cleanup_zero=self.idle_override is not False, lease_revision=record["revision"],
        )


class Commands:
    def __init__(self):
        self.calls = []
        self.paused = False
        self.receipt = False
        self.fail_pause = False
        self.policies = {kind: {"name": "unless-stopped", "count": 0}
                         for kind in baseline.WORKERS}

    def run(self, instance_id, mode, plan):
        self.calls.append(mode)
        if mode == "pause":
            self.receipt = True
            if self.fail_pause:
                raise WindowHold("partial pause; HOLD")
            self.paused = True
        elif mode == "resume":
            self.paused = False
        return {"status": "READBACK", "api_healthy": True, "receipt": self.receipt,
                "original_policies": self.policies if self.receipt else {},
                "workers": {kind: {"running": not self.paused,
                                   "pid": 0 if self.paused else 100 + index,
                                   "policy": {"name": "no", "count": 0} if self.paused else self.policies[kind],
                                   "health": None if self.paused else "healthy"}
                            for index, kind in enumerate(baseline.WORKERS)}}


class BaselineConsumerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        images = {key: f"{REGISTRY}/academy-{role}@sha256:" + str(index + 1) * 64
                  for index, (key, role) in enumerate(IMAGE_ROLES.items())}
        self.snapshot = {
            "schemaVersion": 1, "owner": OWNER, "lock_owner": "candidate:123:1",
            "capacity": 1, "instance_id": BASELINE_ID,
            "profile": f"arn:aws:iam::{baseline.ACCOUNT}:instance-profile/academy-api-development",
            "ami": "ami-" + "e" * 17, "instance_type": "t4g.large",
            "release": RELEASE, "images": images, "api_version": 3,
            "workers_version": 4, "production_database": "academy_api_production",
        }
        self.path = Path(self.temp.name) / "baseline.json"
        raw = json.dumps(self.snapshot, sort_keys=True).encode()
        self.path.write_bytes(raw)
        self.digest = hashlib.sha256(raw).hexdigest()
        self.record = {
            "lease_id": LEASE_ID, "owner_task": OWNER, "lock_owner": "candidate:123:1",
            "source_sha": SOURCE, "images": {kind: "sha256:" + "f" * 64 for kind in
                                               ("api", "ai", "tools", "messaging")},
            "endpoint": f"ssm://{QA_ID}:8000",
            "profile": f"arn:aws:iam::{baseline.ACCOUNT}:instance-profile/academy-api-qa",
            "scope": [509, 511], "baseline_sha256": self.digest,
            "tenant_ids": [17], "message_key_version": 1, "resource_manifest_sha256": "e"*64,
            "state": "prepared", "revision": 1, "started_at": 900,
            "renewed_at": 900, "expires_at": 1500,
        }
        self.ec2 = EC2(self.snapshot, self.record)
        self.ssm = SSM()
        self.sqs = SQS()
        self.store = Store(self.record)
        self.runtime = Runtime(self.record)
        self.commands = Commands()
        module = types.ModuleType("scripts.v1.candidate_qa_observer")
        module.AWSRuntimeAdapter = Runtime
        module.BootstrapEvidence = BootstrapEvidence
        module.QueueExclusivityEvidence = QueueExclusivityEvidence
        self.modules = patch.dict(sys.modules, {"scripts.v1.candidate_qa_observer": module})
        self.modules.start(); self.addCleanup(self.modules.stop)
        self.main = patch("scripts.v1.candidate_qa_launch.assert_main_controller", return_value="main")
        self.main.start(); self.addCleanup(self.main.stop)
        sts = types.SimpleNamespace(get_caller_identity=lambda: {
            "Account": baseline.ACCOUNT,
            "Arn": f"arn:aws:sts::{baseline.ACCOUNT}:assumed-role/academy-gha-candidate-production/test",
        })
        self.subject = baseline.BaselineConsumers(
            ec2=self.ec2, ssm=self.ssm, sqs=self.sqs, sts=sts,
            store=self.store, runtime=self.runtime, clock=lambda: 1000,
            commands=self.commands,
        )
        self.runtime.queue_exclusivity_probe = lambda record: self.subject.observe_exclusivity(
            record, snapshot_path=self.path, snapshot_sha256=self.digest,
        )

    def pause(self):
        return self.subject.pause(self.record, snapshot_path=self.path,
                                  snapshot_sha256=self.digest)

    def resume(self):
        return self.subject.resume(self.record, snapshot_path=self.path,
                                   snapshot_sha256=self.digest)

    def test_pause_requires_bootstrap_then_separate_idle_readback(self):
        proof = self.pause()
        self.assertIsInstance(proof, QueueExclusivityEvidence)
        self.assertEqual(self.runtime.bootstrap_calls, 1)
        self.assertEqual(self.commands.calls, ["inspect", "pause", "inspect"])
        self.assertEqual(proof.baseline_worker_pids,
                         {kind: () for kind in baseline.WORKERS})
        self.assertEqual((proof.lease_id, proof.binding_sha256, proof.baseline_snapshot_sha256),
                         (LEASE_ID, binding(self.record), self.digest))

    def test_active_observe_reproves_exclusivity_without_cached_pause_result(self):
        self.pause()
        self.record.update(state="active", revision=2)
        first = self.subject.observe_exclusivity(
            self.record, snapshot_path=self.path, snapshot_sha256=self.digest,
        )
        self.assertEqual(first.binding_sha256, binding(self.record))
        self.assertEqual(self.commands.calls, ["inspect", "pause", "inspect", "inspect"])
        self.sqs.counts["tools"]["ApproximateNumberOfMessagesDelayed"] = "1"
        active = self.subject.observe_exclusivity(
            self.record, snapshot_path=self.path, snapshot_sha256=self.digest,
        )
        self.assertEqual(active.baseline_worker_pids,
                         {kind: () for kind in baseline.WORKERS})
        self.assertEqual(self.commands.calls[-1], "inspect")
        self.sqs.counts["tools"]["ApproximateNumberOfMessagesDelayed"] = "invalid"
        with self.assertRaises(ValueError):
            self.subject.observe_exclusivity(
                self.record, snapshot_path=self.path, snapshot_sha256=self.digest,
            )

    def test_missing_bootstrap_adapter_cannot_pause(self):
        self.subject.runtime = object()
        with self.assertRaises(WindowHold):
            self.pause()
        self.assertEqual(self.commands.calls, [])

    def test_expired_prepared_lease_keeps_baseline_running(self):
        self.record["expires_at"] = 1020
        with self.assertRaises(ValueError):
            self.pause()
        self.assertEqual(self.runtime.bootstrap_calls, 0)
        self.assertEqual(self.commands.calls, [])

    def test_wrong_snapshot_hash_or_owner_cannot_pause(self):
        with self.assertRaises(ValueError):
            self.subject.pause(self.record, snapshot_path=self.path,
                               snapshot_sha256="0" * 64)
        self.record["owner_task"] = "foreign"
        with self.assertRaises(ValueError):
            self.pause()
        self.assertEqual(self.commands.calls, [])

    def test_active_ssm_session_prevents_baseline_mutation(self):
        self.ssm.sessions = ({"SessionId": "foreign"},)
        with self.assertRaises(ValueError):
            self.pause()
        self.assertEqual(self.commands.calls, [])

    def test_qa_instance_tag_drift_prevents_baseline_mutation(self):
        tags = self.ec2.instances[1]["Tags"]
        next(tag for tag in tags if tag["Key"] == "CandidateLeaseId")["Value"] = "0" * 32
        with self.assertRaises(ValueError):
            self.pause()
        self.assertEqual(self.commands.calls, [])

    def test_nonzero_queue_after_pause_retains_receipt_and_hold(self):
        self.sqs.counts["ai"]["ApproximateNumberOfMessagesNotVisible"] = "1"
        with self.assertRaises(ValueError):
            self.pause()
        self.assertTrue(self.commands.receipt)
        self.assertEqual(self.commands.calls, ["inspect", "pause", "inspect"])

    def test_partial_pause_retains_receipt_and_cannot_claim_exclusivity(self):
        self.commands.fail_pause = True
        with self.assertRaises(WindowHold):
            self.pause()
        self.assertTrue(self.commands.receipt)
        self.assertEqual(self.commands.calls, ["inspect", "pause"])

    def test_resume_requires_closed_restoration_and_fresh_qa_idle(self):
        self.commands.paused = True
        self.commands.receipt = True
        with self.assertRaises(ValueError):
            self.resume()
        self.record.update(state="closed", restore_ready=True, revision=2)
        self.runtime.idle_override = False
        with self.assertRaises(ValueError):
            self.resume()
        self.assertEqual(self.commands.calls, ["inspect"])

    def test_resume_starts_exact_original_containers_after_idle_proof(self):
        self.record.update(state="closed", restore_ready=True, revision=2)
        self.commands.paused = True
        self.commands.receipt = True
        self.resume()
        self.assertEqual(self.runtime.idle_calls, 1)
        self.assertEqual(self.commands.calls, ["inspect", "inspect", "resume", "inspect"])
        self.assertFalse(self.commands.paused)

    def test_resume_holds_when_baseline_queue_reappears(self):
        self.record.update(state="closed", restore_ready=True, revision=2)
        self.commands.paused = True
        self.commands.receipt = True
        self.sqs.counts["messaging"]["ApproximateNumberOfMessages"] = "1"
        with self.assertRaises(ValueError):
            self.resume()
        self.assertEqual(self.commands.calls, ["inspect"])
        self.assertTrue(self.commands.paused)

    def test_host_program_has_receipt_before_mutation_and_no_hard_stop(self):
        compile(baseline.HOST_PROGRAM, "candidate_baseline_host", "exec")
        self.assertLess(baseline.HOST_PROGRAM.index("receipt(plan, current, create=True)"),
                        baseline.HOST_PROGRAM.index('"docker", "update", "--restart=no"'))
        for forbidden in ('"docker", "stop"', '"docker", "kill"',
                          '"docker", "rm"', 'SIGKILL', 'terminate_instances'):
            self.assertNotIn(forbidden, baseline.HOST_PROGRAM)
        self.assertNotIn('"Env":', baseline.HOST_PROGRAM)

    def test_uncertain_ssm_submission_holds_without_retry(self):
        class Uncertain:
            calls = 0

            def send_command(self, **kwargs):
                self.calls += 1
                raise TimeoutError("acknowledgement lost")

        ssm = Uncertain()
        commands = baseline.SSMBaselineCommands(ssm, sleeper=lambda seconds: None)
        with self.assertRaisesRegex(WindowHold, "submission uncertain"):
            commands.run(BASELINE_ID, "pause", {"lease_id": LEASE_ID})
        self.assertEqual(ssm.calls, 1)

    def test_ssm_success_requires_exit_zero_and_typed_readback(self):
        class CommandSSM:
            sent = None
            result_code = 0

            def send_command(self, **kwargs):
                self.sent = kwargs
                return {"Command": {"CommandId": "f" * 8 + "-" + "f" * 4 + "-" +
                                     "f" * 4 + "-" + "f" * 4 + "-" + "f" * 12}}

            def get_command_invocation(self, **kwargs):
                return {"Status": "Success", "ResponseCode": self.result_code,
                        "StandardOutputContent": '{"status":"READBACK"}'}

        ssm = CommandSSM()
        commands = baseline.SSMBaselineCommands(ssm, sleeper=lambda seconds: None)
        plan = self.subject._plan(self.record, self.snapshot, self.digest)
        self.assertEqual(commands.run(BASELINE_ID, "inspect", plan), {"status": "READBACK"})
        self.assertEqual(ssm.sent["InstanceIds"], [BASELINE_ID])
        self.assertNotIn("DB_PASSWORD", ssm.sent["Parameters"]["commands"][0])
        ssm.result_code = 1
        with self.assertRaisesRegex(WindowHold, "exit cleanly"):
            commands.run(BASELINE_ID, "pause", plan)


if __name__ == "__main__":
    unittest.main()
