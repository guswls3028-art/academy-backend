"""Bounded QA lease protocol. Live admission adapter is intentionally not installed.

No workflow opens this window until the owning API/worker tasks implement the
trusted readback interface documented in candidate-preparation.md.
"""
from __future__ import annotations
import copy
import hashlib
import json
import re
import time
from dataclasses import dataclass

LOCK_KEY = "__deployment_control_v2__"
LEASE_KEY = "__candidate_qa_window__"
PROFILE = "arn:aws:iam::809466760795:instance-profile/academy-api-qa"
MAX_SECONDS = 5400
ADMISSION_MARGIN = 30


class WindowHold(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise WindowHold(message)


def binding(record):
    fields = ("lease_id","owner_task","lock_owner","source_sha","images","endpoint","profile","scope","baseline_sha256","tenant_ids","message_key_version","resource_manifest_sha256")
    return hashlib.sha256(json.dumps({k:record[k] for k in fields},sort_keys=True,separators=(",",":")).encode()).hexdigest()


def specification(*, lease_id, owner_task, lock_owner, source_sha, images, endpoint,
                  profile, scope, baseline_sha256, tenant_ids, message_key_version, resource_manifest_sha256):
    require(re.fullmatch(r"[0-9a-f]{32}",lease_id), "Invalid lease ID")
    require(re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}",owner_task), "Exact owner task ID required")
    require(re.fullmatch(r"candidate:[1-9][0-9]*:[1-9][0-9]*",lock_owner), "Workflow lock owner required")
    require(re.fullmatch(r"[0-9a-f]{40}",source_sha), "Exact candidate source required")
    require(set(images) == {"api","tools","ai","messaging"}, "All runtime digests required")
    require(all(re.fullmatch(r"sha256:[0-9a-f]{64}",v) for v in images.values()), "Immutable digests required")
    require(re.fullmatch(r"ssm://i-[0-9a-f]{17}:8000",endpoint), "QA-only admission endpoint required")
    require(profile == PROFILE, "Isolated QA profile required")
    require(bool(scope) and set(scope) <= {509,511} and len(scope) == len(set(scope)), "Unapproved acceptance scope")
    require(re.fullmatch(r"[0-9a-f]{64}",baseline_sha256), "Rollback snapshot required")
    require(isinstance(tenant_ids,list) and tenant_ids and
            all(type(v) is int and v > 0 for v in tenant_ids) and len(set(tenant_ids)) == len(tenant_ids),
            "Exact disposable QA tenant IDs required")
    require(type(message_key_version) is int and message_key_version > 0, "Pinned QA signing key version required")
    require(isinstance(resource_manifest_sha256,str) and re.fullmatch(r"[0-9a-f]{64}",resource_manifest_sha256),
            "Exact creator resource manifest required")
    return dict(resource_manifest_sha256=resource_manifest_sha256, tenant_ids=sorted(tenant_ids), message_key_version=message_key_version, lease_id=lease_id, owner_task=owner_task, lock_owner=lock_owner, source_sha=source_sha,
                images=copy.deepcopy(images), endpoint=endpoint, profile=profile, scope=sorted(scope),
                baseline_sha256=baseline_sha256)


@dataclass(frozen=True)
class InertReadback:
    binding_sha256: str
    observed_at: int
    instance_id: str
    profile: str
    managed: bool
    containers: int
    active_sessions: int


@dataclass(frozen=True)
class Readback:
    binding_sha256: str
    observed_at: int
    api_admission: str
    workers_receiving: bool
    workers_inflight: dict
    queue_counts: dict
    active_sessions: tuple
    enforcement_expires_at: int
    cleanup_zero: bool
    lease_revision: int
    preflight_ready: bool = False


@dataclass(frozen=True)
class PreparedCleanup:
    binding_sha256: str
    observed_at: int
    key_revoked: bool
    resources_zero: bool
    baseline_unchanged: bool


class RuntimeAdapter:
    """Must query the exact runtime via a trusted channel; caller JSON is not proof."""
    def observe_inert(self, record):
        raise WindowHold("Trusted inert instance identity adapter not installed")

    def observe_preflight(self, record):
        raise WindowHold("Trusted initial fixture preflight adapter not installed")

    def observe(self, record):
        raise WindowHold("QA API admission / worker in-flight adapter not installed")

    def drain(self, record):
        raise WindowHold("QA API admission / worker drain adapter not installed")

    def abort_prepared(self, record):
        raise WindowHold("Trusted prepared-key/resource cleanup adapter not installed")


def verify_readback(record, proof, now, *, idle=False, drained=False, preflight=False):
    require(isinstance(proof,Readback), "Typed runtime readback required")
    require(proof.binding_sha256 == binding(record), "Runtime belongs to another lease/candidate")
    require(0 <= now-proof.observed_at <= 10, "Stale runtime readback")
    require(type(proof.lease_revision) is int and proof.lease_revision == record["revision"],
            "Runtime lease revision differs")
    require(proof.enforcement_expires_at == record["expires_at"], "Runtime expiry differs")
    require(proof.api_admission == ("closed" if drained else "lease-bound"),
            "Runtime API admission is not enforced")
    require(not drained or not proof.workers_receiving, "Workers still accept new jobs")
    require(set(proof.workers_inflight) == {"ai","tools","messaging"}, "Worker inventory incomplete")
    require(set(proof.queue_counts) == {"ai","tools","messaging"}, "Queue inventory incomplete")
    for counts in proof.queue_counts.values():
        require(set(counts) == {"visible","inflight","delayed"} and
                all(type(v) is int and v >= 0 for v in counts.values()), "Queue readback malformed")
    require(all(isinstance(v,tuple) for v in proof.workers_inflight.values()), "Exact in-flight IDs required")
    if idle:
        if preflight:
            require(proof.preflight_ready is True, "QA initial fixture preflight not verified")
        else:
            require(proof.cleanup_zero is True, "QA cleanup not verified")
        require(not proof.active_sessions and not any(proof.workers_inflight.values())
                and all(not any(v.values()) for v in proof.queue_counts.values()),
                "Active session, queue or worker remains; HOLD")


class LeaseStore:
    def __init__(self, client, table="academy-v1-video-job-lock"):
        self.client, self.table = client, table

    def read(self):
        item=self.client.get_item(TableName=self.table,Key={"videoId":{"S":LEASE_KEY}},ConsistentRead=True).get("Item")
        return json.loads(item["record"]["S"]) if item else None

    def commit(self, record, expected_revision, now):
        # One transaction both renews the exact shared lock and advances the lease.
        # No TTL on the lease item: expiration must not erase unresolved HOLDs.
        put={"TableName":self.table,"Item":{"videoId":{"S":LEASE_KEY},
             "revision":{"N":str(record["revision"])},"record":{"S":json.dumps(record,sort_keys=True)}},
             "ConditionExpression":"attribute_not_exists(videoId)" if expected_revision is None else
                                   "revision = :revision AND leaseId = :lease"}
        put["Item"]["leaseId"]={"S":record["lease_id"]}
        put["Item"]["state"]={"S":record["state"]}
        if expected_revision is not None:
            put["ExpressionAttributeValues"]={":revision":{"N":str(expected_revision)},":lease":{"S":record["lease_id"]}}
        self.client.transact_write_items(TransactItems=[
            {"Update":{"TableName":self.table,"Key":{"videoId":{"S":LOCK_KEY}},
               "UpdateExpression":"SET #ttl = :until",
               "ConditionExpression":"#owner = :owner AND #ttl > :now",
               "ExpressionAttributeNames":{"#owner":"owner","#ttl":"ttl"},
               "ExpressionAttributeValues":{":owner":{"S":record["lock_owner"]},":now":{"N":str(now)},
                                            ":until":{"N":str(max(now+300,record["expires_at"]+2400))}}}},
            {"Put":put}])


class Window:
    def __init__(self, store, runtime=None, clock=time.time):
        self.store=store
        self.runtime=runtime or RuntimeAdapter()
        self.clock=clock

    def prepare(self, spec, seconds):
        spec=specification(**spec)
        now=int(self.clock())
        require(type(seconds) is int and 60 <= seconds <= MAX_SECONDS, "Window duration outside bound")
        require(self.store.read() is None, "Existing lease or HOLD must be reconciled first")
        proof=self.runtime.observe_inert(spec)
        require(isinstance(proof,InertReadback) and proof.binding_sha256 == binding(spec)
                and 0 <= now-proof.observed_at <= 10
                and proof.instance_id == spec["endpoint"].split("//",1)[1].split(":")[0]
                and proof.profile == spec["profile"] and proof.managed is True
                and proof.containers == 0 and proof.active_sessions == 0,
                "Inert instance identity, sessions or container inventory differs")
        record=dict(spec,state="prepared",control_hold=False,revision=1,started_at=now,renewed_at=now,expires_at=now+seconds)
        self.store.commit(record,None,now)
        return record

    def open(self, spec, seconds):
        spec=specification(**spec);now=int(self.clock())
        record=self.owned(spec["lease_id"],spec["owner_task"])
        require(record["state"] == "prepared" and not record.get("control_hold") and binding(record) == binding(spec)
                and record["expires_at"]-record["started_at"] == seconds
                and now < record["expires_at"]-ADMISSION_MARGIN,
                "No exact unexpired prepared lease")
        # Containers have validated the committed PREPARED record but cannot
        # authenticate, mutate or poll queues until this atomic transition.
        verify_readback(record,self.runtime.observe_preflight(record),int(self.clock()),idle=True,drained=True,preflight=True)
        revision=record["revision"]
        record.update(state="active",revision=revision+1)
        self.store.commit(record,revision,now)
        try:
            verify_readback(record,self.runtime.observe_preflight(record),int(self.clock()),idle=True,preflight=True)
        except Exception:
            revision=record["revision"]
            record.update(control_hold=True,revision=revision+1)
            self.store.commit(record,revision,now)
            raise WindowHold("Active runtime readback failed; endpoint must remain unpublished")
        return record

    def owned(self, lease_id, owner_task):
        record=self.store.read()
        require(record and record["lease_id"] == lease_id and record["owner_task"] == owner_task,
                "Forged or replayed lease owner")
        return record

    def admit(self, lease_id, owner_task, pull_request):
        record=self.owned(lease_id,owner_task); now=int(self.clock())
        require(record["state"] == "active" and not record.get("control_hold") and now < record["expires_at"]-ADMISSION_MARGIN,
                "QA window closing; new work rejected, retain existing results")
        require(pull_request in record["scope"], "Action outside approved acceptance scope")
        verify_readback(record,self.runtime.observe(record),int(self.clock()))
        return binding(record)

    def renew(self, lease_id, owner_task, seconds):
        record=self.owned(lease_id,owner_task); now=int(self.clock())
        require(record["state"]=="active" and not record.get("control_hold") and now < record["expires_at"]-ADMISSION_MARGIN,
                "Expired or draining leases cannot be renewed")
        require(type(seconds) is int and seconds > 0 and now+seconds <= record["started_at"]+MAX_SECONDS
                and now+seconds > record["expires_at"], "Renewal exceeds bounded window")
        # Runtime gates can observe only committed leases. Verify the current
        # revision first, then require every runtime to observe the winning CAS.
        verify_readback(record,self.runtime.observe(record),int(self.clock()))
        revision=record["revision"]
        record.update(revision=revision+1,renewed_at=now,expires_at=now+seconds)
        self.store.commit(record,revision,now)
        try:
            verify_readback(record,self.runtime.observe(record),int(self.clock()))
        except Exception:
            revision=record["revision"]
            record.update(control_hold=True,revision=revision+1)
            self.store.commit(record,revision,int(self.clock()))
            raise WindowHold("Renewed runtime revision unverified; admissions held") from None
        return record

    def begin_drain(self, lease_id, owner_task):
        record=self.owned(lease_id,owner_task); now=int(self.clock())
        require(record["state"] in ("active","draining"), "Closed lease cannot drain again")
        if record["state"] != "draining":
            revision=record["revision"]
            record.update(state="draining",revision=revision+1,
                          drain_from_revision=record.get("drain_from_revision",revision))
            self.store.commit(record,revision,now)
        self.runtime.drain(record)
        return record

    def finish(self, lease_id, owner_task, completion=None):
        record=self.owned(lease_id,owner_task); now=int(self.clock())
        require(record["state"] in ("active","draining"), "Lease already completed; replay rejected")
        expired=now >= record["expires_at"]
        require(completion is not None or expired, "Owner completion or actual timeout required")
        if completion is not None:
            require(completion.get("binding_sha256") == binding(record), "Completion candidate differs")
            require(set(completion.get("scope",[])) == set(record["scope"]), "Completion scope incomplete")
            required=("role_results","save_reload","tenant_permission","failure_recovery")
            require(all(isinstance(completion.get(k),dict) and completion[k] and
                        all(v == "pass" for v in completion[k].values()) for k in required),
                    "Actual acceptance evidence incomplete")
            require(completion.get("cleanup_zero") is True and
                    re.fullmatch(r"[0-9a-f]{64}",completion.get("evidence_sha256","")),
                    "Cleanup or immutable evidence missing")
        revision=record["revision"]
        try:
            record=self.begin_drain(lease_id,owner_task)
            revision=record["revision"]
            verify_readback(record,self.runtime.observe(record),int(self.clock()),idle=True,drained=True)
        except Exception:
            record=self.owned(lease_id,owner_task)
            revision=record["revision"]
            record.update(control_hold=True,revision=revision+1)
            self.store.commit(record,revision,now)
            raise WindowHold("Admission/drain/cleanup unverified; retain rollback coordinates")
        record.update(state="closed",control_hold=False,restore_ready=True,revision=revision+1,
                      completion=completion,closed_at=now)
        self.store.commit(record,revision,now)
        return record

    def abort_prepared(self, lease_id, owner_task):
        record=self.owned(lease_id,owner_task);now=int(self.clock())
        require(record["state"]=="prepared", "Only an unopened lease can use inert cleanup")
        revision=record["revision"]
        try:
            proof=self.runtime.abort_prepared(record)
            require(isinstance(proof,PreparedCleanup) and proof.binding_sha256==binding(record)
                    and 0 <= now-proof.observed_at <= 10 and proof.key_revoked is True
                    and proof.resources_zero is True and proof.baseline_unchanged is True,
                    "Prepared cleanup/revocation readback incomplete")
        except Exception:
            record.update(control_hold=True,revision=revision+1)
            self.store.commit(record,revision,now)
            raise WindowHold("Prepared cleanup incomplete; baseline and journal retained") from None
        record.update(state="closed",control_hold=False,restore_ready=False,
                      revision=revision+1,closed_at=now)
        self.store.commit(record,revision,now)
        return record

    def restore_admission(self, lease_id, owner_task):
        record=self.owned(lease_id,owner_task); now=int(self.clock())
        require(record["state"]=="closed" and record.get("restore_ready") is True and not record.get("control_hold"),
                "QA completion/timeout is not reconciled")
        verify_readback(record,self.runtime.observe(record),int(self.clock()),idle=True,drained=True)
        return record["baseline_sha256"]
