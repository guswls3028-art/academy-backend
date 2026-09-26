"""Offline fixtures only: no AWS, SSM, Docker, queue or tenant mutations."""
from __future__ import annotations

import copy
import json

import pytest

from scripts.v1.candidate_qa_observer import (
    ACCOUNT, REGION, AWSRuntimeAdapter, BootstrapEvidence, CleanupEvidence, CONTAINERS,
    FixedSsmProbe, QueueExclusivityEvidence, QUEUE_ATTRS, QUEUE_NAMES,
)
from scripts.v1.candidate_qa_probe import INSIDE_CODE
from scripts.v1.candidate_qa_window import WindowHold, binding, verify_readback


NOW = 1000


def _record(state="active"):
    return {
        "lease_id": "a" * 32, "owner_task": "01a0d04a-d64f-7473-9f58-61a8e983dcc0",
        "lock_owner": "candidate:123:1", "source_sha": "b" * 40,
        "images": {kind: "sha256:" + "c" * 64 for kind in CONTAINERS},
        "endpoint": "ssm://i-0123456789abcdef0:8000",
        "profile": f"arn:aws:iam::{ACCOUNT}:instance-profile/academy-api-qa",
        "scope": [509, 511], "baseline_sha256": "d" * 64,
        "tenant_ids": [101], "message_key_version": 1,
        "state": state, "revision": 2, "started_at": 900,
        "renewed_at": 900, "expires_at": 1600, "control_hold": False,
    }


def _snapshot(record, kind, pid):
    process_id = f"{pid:032x}"
    return {
        "filename": f"{kind}-{pid}-{process_id}.json", "mtime": NOW,
        "data": {
            "enabled": True, "kind": kind, "pid": pid,
            "process_id": process_id, "observed_at": NOW, "alive": True,
            "lease_id": record["lease_id"], "binding_sha256": binding(record),
            "revision": record["revision"], "enforcement_expires_at": record["expires_at"],
            "lease_verified": True, "control_hold": False,
            "admission": "open" if record["state"] == "active" else "closed",
            "inflight": [], "holds": [],
        },
    }


def _probe(record, *, inert=False):
    if inert:
        return {"schema_version": 1, "observed_at": NOW,
                "required_env_exact": True, "inert_ready": True,
                "container_names": [], "containers": {}}
    containers = {}
    for kind, (name, repository) in CONTAINERS.items():
        ref = f"{ACCOUNT}.dkr.ecr.{REGION}.amazonaws.com/{repository}@{record['images'][kind]}"
        env = {
            "ACADEMY_RUNTIME_ENV": "development", "ACADEMY_QA_MODE": "isolated-qa",
            "CANDIDATE_LEASE_REQUIRED": "true", "ACADEMY_QA_LEASE_ID": record["lease_id"],
            "ACADEMY_QA_BINDING_SHA256": binding(record),
            "ACADEMY_QA_MESSAGE_KEY_VERSION": "1",
        }
        if kind == "api":
            env["GUNICORN_WORKERS"] = "2"
            processes = [
                {"pid": 1, "ppid": 0, "gunicorn": True, "command": "api", "started_at": 900},
                {"pid": 2, "ppid": 1, "gunicorn": True, "command": "api", "started_at": 900},
                {"pid": 3, "ppid": 1, "gunicorn": True, "command": "api", "started_at": 900},
            ]
            snapshots = [_snapshot(record, kind, pid) for pid in (2, 3)]
        else:
            processes = [{"pid": 1, "ppid": 0, "gunicorn": False,
                          "command": kind, "started_at": 900}]
            snapshots = [_snapshot(record, kind, 1)]
        if kind == "ai":
            env.update({f"AI_SQS_QUEUE_NAME_{tier}": QUEUE_NAMES["ai"]
                        for tier in ("LITE", "BASIC", "PREMIUM")})
        if kind == "tools":
            env["TOOLS_SQS_QUEUE_NAME"] = QUEUE_NAMES["tools"]
        if kind == "messaging":
            env.update(MESSAGING_SQS_QUEUE_NAME=QUEUE_NAMES["messaging"], SOLAPI_MOCK="true")
        containers[kind] = {
            "name": name, "status": "running", "health": "healthy", "image_ref": ref,
            "image_id": "sha256:" + "f" * 64, "actual_image_id": "sha256:" + "f" * 64,
            "repo_digests": [ref], "env": env,
            "cmd": ["trusted-image-command"], "image_cmd": ["trusted-image-command"],
            "entrypoint": None, "image_entrypoint": None,
            "network_mode": "host", "ports": {},
            "inside": {"observed_at": NOW, "processes": processes, "snapshots": snapshots},
        }
    return {"schema_version": 1, "observed_at": NOW, "required_env_exact": True,
            "inert_ready": False, "api_health": True,
            "container_names": [item[0] for item in CONTAINERS.values()],
            "containers": containers}


class FakeEc2:
    def __init__(self, record):
        self.record = record
        self.calls = []
        self.inbound = []

    def describe_instances(self, **kwargs):
        self.calls.append(kwargs)
        record = self.record
        run, attempt = record["lock_owner"].split(":")[1:]
        tags = {
            "Name": "academy-v1-api-development", "Project": "academy",
            "ManagedBy": "academy-api-development", "QaMode": "isolated-qa",
            "SlotLeaseOwner": record["owner_task"], "CandidateLeaseId": record["lease_id"],
            "CandidateSourceSha": record["source_sha"],
            "ReleaseId": f"sha-{record['source_sha']}-run-{run}-{attempt}",
        }
        return {"Reservations": [{"Instances": [{
            "InstanceId": "i-0123456789abcdef0", "State": {"Name": "running"},
            "IamInstanceProfile": {"Arn": record["profile"]},
            "VpcId": "vpc-123", "SecurityGroups": [{"GroupId": "sg-123", "GroupName": "academy-v1-api-development-sg"}],
            "NetworkInterfaces": [{"Groups": [{"GroupId": "sg-123", "GroupName": "academy-v1-api-development-sg"}]}],
            "Tags": [{"Key": key, "Value": value} for key, value in tags.items()],
        }]}]}

    def describe_security_groups(self, **kwargs):
        self.calls.append(kwargs)
        return {"SecurityGroups": [{"GroupId": "sg-123", "GroupName": "academy-v1-api-development-sg",
                                    "VpcId": "vpc-123", "IpPermissions": self.inbound}]}


class FakeSsm:
    def __init__(self):
        self.sessions = []

    def describe_instance_information(self, **kwargs):
        return {"InstanceInformationList": [{"InstanceId": "i-0123456789abcdef0",
                "PingStatus": "Online", "ResourceType": "EC2Instance"}]}

    def describe_sessions(self, **kwargs):
        return {"Sessions": self.sessions}


class FakeSqs:
    def __init__(self):
        self.values = {kind: ["0", "0", "0"] for kind in QUEUE_NAMES}

    def get_queue_url(self, *, QueueName):
        return {"QueueUrl": f"https://sqs.{REGION}.amazonaws.com/{ACCOUNT}/{QueueName}"}

    def get_queue_attributes(self, *, QueueUrl, AttributeNames):
        kind = next(kind for kind, name in QUEUE_NAMES.items() if QueueUrl.endswith(name))
        return {"Attributes": dict(zip(QUEUE_ATTRS, self.values[kind]))}


class FakeDdb:
    def __init__(self, record):
        self.record = record
        self.calls = []

    def get_item(self, **kwargs):
        self.calls.append(("get", kwargs))
        return {"Item": {"owner": {"S": self.record["lock_owner"]}, "ttl": {"N": "2000"}}}

    def transact_get_items(self, **kwargs):
        self.calls.append(("transaction", kwargs))
        record = self.record
        return {"Responses": [
            {"Item": {"record": {"S": json.dumps(record)}, "leaseId": {"S": record["lease_id"]},
                      "revision": {"N": str(record["revision"])}}},
            {"Item": {"owner": {"S": record["lock_owner"]}, "ttl": {"N": "2000"}}},
        ]}


def _adapter(record, probe, *, cleanup=True, exclusivity=True):
    def cleanup_proof(_record):
        return CleanupEvidence(record["lease_id"], binding(record), NOW,
                               tuple(record["tenant_ids"]), "e" * 64,
                               {"exam": {"db_rows": 0, "r2_objects": 0,
                                         "pending_outboxes": 0}})

    def exclusivity_proof(_record):
        return QueueExclusivityEvidence(record["lease_id"], binding(record), NOW,
                                        record["baseline_sha256"], "i-11111111111111111",
                                        {kind: () for kind in QUEUE_NAMES})

    ec2, ssm, sqs, ddb = FakeEc2(record), FakeSsm(), FakeSqs(), FakeDdb(record)
    adapter = AWSRuntimeAdapter(
        expected_slot_owner=record["owner_task"], ec2=ec2, ssm=ssm, sqs=sqs, ddb=ddb,
        probe=lambda instance_id: probe,
        cleanup_probe=cleanup_proof if cleanup else None,
        queue_exclusivity_probe=exclusivity_proof if exclusivity else None,
        cleanup_domains=("exam",), clock=lambda: NOW, sleeper=lambda seconds: None,
    )
    return adapter, ec2, ssm, sqs, ddb


def test_inert_requires_exact_identity_no_containers_or_sessions():
    record = _record("prepared")
    adapter, ec2, ssm, _, ddb = _adapter(record, _probe(record, inert=True), cleanup=False, exclusivity=False)
    proof = adapter.observe_inert(record)
    assert proof.containers == 0 and proof.active_sessions == 0 and proof.managed is True
    assert ec2.calls == [{"InstanceIds": ["i-0123456789abcdef0"]},
                         {"GroupIds": ["sg-123"]}]
    assert ddb.calls[0][0] == "get"
    ssm.sessions = [{"SessionId": "session-1", "Target": "i-0123456789abcdef0"}]
    with pytest.raises(WindowHold):
        adapter.observe_inert(record)


def test_exact_attached_security_group_must_have_no_inbound_rules():
    record = _record("prepared")
    adapter, ec2, _, _, _ = _adapter(record, _probe(record, inert=True),
                                      cleanup=False, exclusivity=False)
    ec2.inbound = [{"IpProtocol": "tcp", "FromPort": 8000,
                    "ToPort": 8000, "IpRanges": [{"CidrIp": "0.0.0.0/0"}]}]
    with pytest.raises(WindowHold, match="inbound"):
        adapter.observe_inert(record)


def test_active_readback_matches_each_live_pid_and_three_queues():
    record = _record()
    adapter, _, _, _, ddb = _adapter(record, _probe(record))
    result = adapter.observe(record)
    assert result.api_admission == "lease-bound" and result.workers_receiving is True
    assert result.lease_revision == 2 and result.cleanup_zero is True
    assert set(result.workers_inflight) == {"ai", "tools", "messaging"}
    assert all(counts == {"visible": 0, "inflight": 0, "delayed": 0}
               for counts in result.queue_counts.values())
    assert result.active_sessions == ()
    assert ddb.calls[0][0] == "transaction"
    verify_readback(record, result, NOW, idle=True)


def test_prepared_bootstrap_proves_closed_workers_before_baseline_pause_without_cleanup():
    record = _record("prepared")
    adapter, _, _, _, ddb = _adapter(record, _probe(record), cleanup=False, exclusivity=False)
    proof = adapter.observe_bootstrap(record)
    assert isinstance(proof, BootstrapEvidence)
    assert proof.lease_revision == record["revision"] and proof.api_worker_pids == (2, 3)
    assert proof.container_names == tuple(item[0] for item in CONTAINERS.values())
    assert ddb.calls[0][0] == "transaction"
    with pytest.raises(WindowHold):
        verify_readback(record, proof, NOW, idle=True)
    with pytest.raises(WindowHold):
        adapter.observe(record)  # full open proof still requires cleanup/exclusivity

    record["state"] = "active"
    with pytest.raises(WindowHold):
        adapter.observe_bootstrap(record)


@pytest.mark.parametrize("missing", ["cleanup", "exclusivity"])
def test_missing_owned_proof_is_hold_not_implicit_zero(missing):
    record = _record()
    adapter, *_ = _adapter(record, _probe(record), cleanup=missing != "cleanup",
                           exclusivity=missing != "exclusivity")
    with pytest.raises(WindowHold):
        adapter.observe(record)


def test_nonzero_domain_manifest_or_baseline_worker_remains_hold():
    record = _record()
    adapter, *_ = _adapter(record, _probe(record))
    adapter.cleanup_probe = lambda _record: CleanupEvidence(
        record["lease_id"], binding(record), NOW, (101,), "e" * 64,
        {"exam": {"db_rows": 0, "r2_objects": 1, "pending_outboxes": 0}},
    )
    with pytest.raises(WindowHold, match="cleanup-zero"):
        adapter.observe(record)

    adapter, *_ = _adapter(record, _probe(record))
    adapter.queue_exclusivity_probe = lambda _record: QueueExclusivityEvidence(
        record["lease_id"], binding(record), NOW, record["baseline_sha256"],
        "i-11111111111111111", {"ai": (88,), "tools": (), "messaging": ()},
    )
    with pytest.raises(WindowHold, match="exclusivity"):
        adapter.observe(record)


@pytest.mark.parametrize("change", [
    lambda probe: probe["containers"]["api"]["inside"]["snapshots"].pop(),
    lambda probe: probe["containers"]["api"]["inside"]["snapshots"][0].update(mtime=NOW-11),
    lambda probe: probe["containers"]["ai"]["inside"]["snapshots"][0]["data"].update(pid=9),
    lambda probe: probe["containers"]["api"]["inside"]["processes"][1].update(started_at=NOW+1),
    lambda probe: probe["containers"]["tools"].update(image_ref="forged"),
    lambda probe: probe["containers"]["api"].update(cmd=["other-command"]),
    lambda probe: probe["containers"]["messaging"].update(health="starting"),
    lambda probe: probe["containers"]["api"]["ports"].update({"8000/tcp": [{"HostIp": "0.0.0.0", "HostPort": "8000"}]}),
    lambda probe: probe["containers"]["api"].update(network_mode="bridge"),
    lambda probe: probe.update(api_health=False),
    lambda probe: probe["containers"]["messaging"]["env"].update(SOLAPI_MOCK="false"),
    lambda probe: probe["containers"]["ai"]["inside"]["snapshots"][0]["data"].update(holds=[{"reason":"pending"}]),
])
def test_missing_stale_dead_or_foreign_runtime_evidence_holds(change):
    record = _record()
    probe = _probe(record)
    change(probe)
    adapter, *_ = _adapter(record, probe)
    with pytest.raises(WindowHold):
        adapter.observe(record)


def test_committed_revision_and_lock_must_match_caller_record():
    record = _record()
    adapter, _, _, _, ddb = _adapter(record, _probe(record))
    wrong = copy.deepcopy(record)
    wrong["revision"] += 1
    with pytest.raises(WindowHold):
        adapter.observe(wrong)
    ddb.record = {**record, "control_hold": True}
    with pytest.raises(WindowHold):
        adapter.observe(record)


def test_committed_transition_waits_for_fresh_process_revision_without_accepting_old():
    record = _record()
    stale = _probe(record)
    for container in stale["containers"].values():
        for item in container["inside"]["snapshots"]:
            item["data"]["revision"] = record["revision"] - 1
    fresh = _probe(record)
    adapter, *_ = _adapter(record, fresh)
    observations = iter((stale, fresh))
    waits = []
    adapter._probe = lambda instance_id: next(observations)
    adapter.sleeper = waits.append
    assert adapter.observe(record).lease_revision == record["revision"]
    assert waits == [2]

    adapter._probe = lambda instance_id: stale
    adapter.sleeper = waits.append
    with pytest.raises(WindowHold, match="heartbeat revision pending"):
        adapter.observe(record)


def test_drain_only_reads_closed_committed_activity_never_mutates():
    record = _record("draining")
    adapter, _, _, _, ddb = _adapter(record, _probe(record), cleanup=False, exclusivity=False)
    proof = adapter.drain(record)
    assert proof.api_admission == "closed" and proof.workers_receiving is False
    assert proof.cleanup_zero is False
    assert all(call[0] == "transaction" for call in ddb.calls)
    verify_readback(record, proof, NOW, drained=True)
    record["state"] = "active"
    with pytest.raises(WindowHold):
        adapter.drain(record)


def test_nonzero_queue_or_cleanup_evidence_blocks_idle_readback():
    record = _record()
    adapter, _, _, sqs, _ = _adapter(record, _probe(record))
    sqs.values["ai"] = ["1", "0", "0"]
    result = adapter.observe(record)
    assert result.queue_counts["ai"]["visible"] == 1
    with pytest.raises(WindowHold):
        verify_readback(record, result, NOW, idle=True)


def test_fixed_ssm_probe_uses_only_checked_in_source_and_exact_instance(monkeypatch):
    class Ssm:
        def __init__(self):
            self.sent = None

        def send_command(self, **kwargs):
            self.sent = kwargs
            return {"Command": {"CommandId": "0" * 36}}

        def get_command_invocation(self, **kwargs):
            return {"Status": "Success", "ResponseCode": 0,
                    "StandardOutputContent": '{"schema_version":1}',
                    "StandardErrorContent": ""}

    ssm = Ssm()
    assert FixedSsmProbe(ssm).read("i-0123456789abcdef0") == {"schema_version": 1}
    assert ssm.sent["InstanceIds"] == ["i-0123456789abcdef0"]
    assert ssm.sent["DocumentName"] == "AWS-RunShellScript"
    assert len(ssm.sent["Parameters"]["commands"]) == 1


def test_container_probe_is_self_contained_for_tools_and_messaging_images():
    compile(INSIDE_CODE, "<qa-inside-probe>", "exec")
    assert "from scripts" not in INSIDE_CODE
    assert "subprocess" not in INSIDE_CODE


def test_ssm_eventual_command_visibility_retries_only_that_error():
    class NotVisible(Exception):
        response = {"Error": {"Code": "InvocationDoesNotExist"}}

    class Ssm:
        def __init__(self):
            self.reads = 0

        def send_command(self, **kwargs):
            return {"Command": {"CommandId": "0" * 36}}

        def get_command_invocation(self, **kwargs):
            self.reads += 1
            if self.reads == 1:
                raise NotVisible()
            return {"Status": "Success", "ResponseCode": 0,
                    "StandardOutputContent": '{"schema_version":1}', "StandardErrorContent": ""}

    ssm = Ssm()
    waits = []
    assert FixedSsmProbe(ssm, sleeper=waits.append).read("i-0123456789abcdef0") == {"schema_version": 1}
    assert ssm.reads == 2 and waits == [1]
