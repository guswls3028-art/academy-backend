"""QA-only lease admission and nonsecret process activity readback.

Production and ordinary development do not create an AWS client or a thread.
An explicitly configured QA runtime fails closed when its lease cannot be read.
"""
from __future__ import annotations

import atexit
from contextlib import contextmanager
from functools import lru_cache
import hashlib
import hmac
import copy
import json
import os
from pathlib import Path
import re
import threading
import time
import uuid

LEASE_KEY = "__candidate_qa_window__"
LOCK_KEY = "__deployment_control_v2__"
REGION = "ap-northeast-2"
TABLE = "academy-v1-video-job-lock"
KINDS = frozenset({"api", "ai", "tools", "messaging"})
MARGIN = 30
BINDING_FIELDS = ("lease_id", "owner_task", "lock_owner", "source_sha", "images",
                  "endpoint", "profile", "scope", "baseline_sha256", "tenant_ids", "message_key_version")


class QaLeaseClosed(RuntimeError):
    code = "QA_WINDOW_CLOSED"


def binding_sha256(record):
    return hashlib.sha256(json.dumps({k: record[k] for k in BINDING_FIELDS},
                                     sort_keys=True, separators=(",", ":")).encode()).hexdigest()

class QaMessageInFlight(QaLeaseClosed):
    code = "QA_MESSAGE_INFLIGHT"


class QaMessageCompleted(QaLeaseClosed):
    code = "QA_MESSAGE_COMPLETED"


class MessageClaims:
    """Conditional, nonsecret claim journal; crashed in-flight claims stay HOLD."""
    def __init__(self, client):
        self.client = client

    @staticmethod
    def key(stamp):
        return "__candidate_qa_message__:" + stamp["lease_id"] + ":" + stamp["message_id"]

    def claim(self, stamp):
        key = {"videoId": {"S": self.key(stamp)}}
        old = self.client.get_item(TableName=TABLE, Key=key, ConsistentRead=True).get("Item")
        owner = uuid.uuid4().hex
        identity = hashlib.sha256(_canonical(stamp).encode()).hexdigest()
        if old:
            if old["identity"]["S"] != identity:
                raise QaLeaseClosed("QA message identity was reused")
            state = old["state"]["S"]
            if state == "completed":
                raise QaMessageCompleted("QA message already completed")
            if state != "retryable":
                raise QaMessageInFlight("QA message is already in flight; retain receipt")
            revision = int(old["revision"]["N"])
        else:
            revision = 0
        item = dict(key, identity={"S":identity}, state={"S":"inflight"},
                    claimOwner={"S":owner}, revision={"N":str(revision+1)})
        put = {"TableName":TABLE,"Item":item,
               "ConditionExpression":"attribute_not_exists(videoId)" if not old else
                                     "#revision = :revision AND #state = :retryable"}
        if old:
            put.update(ExpressionAttributeNames={"#revision":"revision","#state":"state"},
                       ExpressionAttributeValues={":revision":{"N":str(revision)},":retryable":{"S":"retryable"}})
        try:
            self.client.transact_write_items(TransactItems=[
                {"ConditionCheck":{"TableName":TABLE,"Key":{"videoId":{"S":LEASE_KEY}},
                    "ConditionExpression":"leaseId = :lease AND revision = :revision AND #state = :active",
                    "ExpressionAttributeNames":{"#state":"state"},
                    "ExpressionAttributeValues":{":lease":{"S":stamp["lease_id"]},
                         ":revision":{"N":str(stamp["revision"])},":active":{"S":"active"}}}},
                {"Put":put}])
        except Exception:
            # Resolve a losing CAS or lost acknowledgement by exact consistent
            # readback; never submit a second blind claim.
            try:
                actual = self.client.get_item(TableName=TABLE,Key=key,ConsistentRead=True).get("Item")
            except Exception:
                actual = None
            if actual and actual.get("identity",{}).get("S") == identity:
                state = actual.get("state",{}).get("S")
                if state == "completed":
                    raise QaMessageCompleted("QA message already completed") from None
                if state == "inflight" and actual.get("claimOwner",{}).get("S") != owner:
                    raise QaMessageInFlight("QA message claimed concurrently") from None
                if state == "inflight" and actual.get("claimOwner",{}).get("S") == owner:
                    return {"key":key,"owner":owner,"revision":revision+1,"identity":identity}
            raise QaLeaseClosed("QA message claim uncertain; preserve receipt and HOLD") from None
        return {"key":key,"owner":owner,"revision":revision+1,"identity":identity}

    def finish(self, claim, completed):
        try:
            self.client.update_item(TableName=TABLE,Key=claim["key"],
                UpdateExpression="SET #state = :state",
                ConditionExpression="claimOwner = :owner AND revision = :revision AND identity = :identity AND #state = :inflight",
                ExpressionAttributeNames={"#state":"state"},
                ExpressionAttributeValues={":state":{"S":"completed" if completed else "retryable"},
                    ":owner":{"S":claim["owner"]},":revision":{"N":str(claim["revision"])},
                    ":identity":{"S":claim["identity"]},":inflight":{"S":"inflight"}})
        except Exception:
            raise QaLeaseClosed("QA message outcome journal unavailable; HOLD") from None


def _canonical(value):
    return json.dumps(value,sort_keys=True,separators=(",",":"),ensure_ascii=False,allow_nan=False)



def decode_qa_json(raw):
    """Reject duplicate keys and non-finite numbers before any QA side effect."""
    def object_pairs(pairs):
        value={}
        for key,item in pairs:
            if key in value:
                raise QaLeaseClosed("Duplicate QA JSON key")
            value[key]=item
        return value
    def constant(_value):
        raise QaLeaseClosed("Non-finite QA JSON number")
    try:
        return json.loads(raw,object_pairs_hook=object_pairs,parse_constant=constant)
    except (ValueError,TypeError):
        raise QaLeaseClosed("Invalid canonical QA JSON") from None

class AdmissionGate:
    def __init__(self, kind, *, context=None, reader=None, clock=time.time,
                 activity_dir=None, heartbeat=True, claims=None):
        if kind not in KINDS:
            raise ValueError("Unknown QA process kind")
        self.kind = kind
        self.context = dict(os.environ if context is None else context)
        self.clock = clock
        self.enabled = self.context.get("ACADEMY_QA_MODE", "") == "isolated-qa"
        required_raw = self.context.get("CANDIDATE_LEASE_REQUIRED", "").lower()
        if required_raw not in ("", "true", "false", "1", "0"):
            raise QaLeaseClosed("Invalid required-lease marker")
        required = required_raw in ("true", "1")
        configured = required or any(self.context.get(k) for k in (
            "ACADEMY_QA_MODE", "ACADEMY_QA_LEASE_ID", "ACADEMY_QA_BINDING_SHA256"))
        if self.enabled and not required:
            raise QaLeaseClosed("Isolated QA must require its committed lease")
        if configured and not self.enabled:
            raise QaLeaseClosed("Invalid QA runtime configuration")
        if self.enabled:
            if (self.context.get("ACADEMY_RUNTIME_ENV") != "development"
                    or not re.fullmatch(r"[0-9a-f]{32}", self.context.get("ACADEMY_QA_LEASE_ID", ""))
                    or not re.fullmatch(r"[0-9a-f]{64}", self.context.get("ACADEMY_QA_BINDING_SHA256", ""))):
                raise QaLeaseClosed("Incomplete isolated QA identity")
        self._reader = reader
        self._aws_client = None
        self._clock = clock
        self._heartbeat_enabled = heartbeat
        self._directory = Path(activity_dir or self.context.get(
            "ACADEMY_QA_ACTIVITY_DIR", "/tmp/academy-qa-activity"))
        self._mutex = threading.RLock()
        self._active = {}
        self._holds = []
        self._claims = claims
        self._claim_context = {}
        self._record = None
        self._valid = False
        self._pid = os.getpid()
        self._process_id = uuid.uuid4().hex
        self._thread = None
        self._stop = threading.Event()
        if self.enabled:
            if hasattr(os, "register_at_fork"):
                os.register_at_fork(after_in_child=self._after_fork)
            atexit.register(self.close)
            self._start()

    def _after_fork(self):
        # Gunicorn preload must not inherit a locked mutex or a dead thread.
        self._mutex = threading.RLock()
        self._active = {}
        self._holds = []
        self._claims = None
        self._claim_context = {}
        self._record = None
        self._valid = False
        self._pid = os.getpid()
        self._process_id = uuid.uuid4().hex
        self._thread = None
        self._stop = threading.Event()
        self._aws_client = None
        self._start()

    def _start(self):
        self._directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self._heartbeat_enabled:
            self._thread = threading.Thread(target=self._heartbeat, name="qa-lease-readback", daemon=True)
            self._thread.start()

    def _read(self):
        if self._reader is not None:
            return self._reader()
        if self._aws_client is None:
            import boto3
            from botocore.config import Config
            self._aws_client = boto3.client("dynamodb", region_name=REGION,
                config=Config(connect_timeout=2, read_timeout=2, retries={"max_attempts": 1}))
        response = self._aws_client.transact_get_items(TransactItems=[
            {"Get": {"TableName": TABLE, "Key": {"videoId": {"S": LEASE_KEY}}}},
            {"Get": {"TableName": TABLE, "Key": {"videoId": {"S": LOCK_KEY}}}},
        ])
        rows = response["Responses"]
        item, lock = rows[0]["Item"], rows[1]["Item"]
        record = json.loads(item["record"]["S"])
        if record["lease_id"] != item["leaseId"]["S"] or record["revision"] != int(item["revision"]["N"]):
            raise QaLeaseClosed("Lease revision mismatch")
        return record, {"owner": lock["owner"]["S"], "expires_at": int(lock["ttl"]["N"])}

    def _observe(self):
        now = int(self.clock())
        try:
            record, lock = self._read()
            if (record["lease_id"] != self.context["ACADEMY_QA_LEASE_ID"]
                    or binding_sha256(record) != self.context["ACADEMY_QA_BINDING_SHA256"]
                    or record["profile"] != "arn:aws:iam::809466760795:instance-profile/academy-api-qa"
                    or record["lock_owner"] != lock["owner"] or lock["expires_at"] <= now
                    or type(record["revision"]) is not int or record["revision"] < 1
                    or not record["scope"] or not set(record["scope"]) <= {509, 511}
                    or not record["started_at"] <= record["renewed_at"] <= now
                    or not record["started_at"] < record["expires_at"] <= record["started_at"] + 5400):
                raise QaLeaseClosed("QA identity or mutation lock differs")
        except Exception:
            self._valid = False
            raise QaLeaseClosed("QA window unavailable; retain results and contact its owner") from None
        self._record = record
        self._valid = True
        return record

    def _is_open(self):
        return (not self._holds and self._valid and self._record is not None and not self._record.get("control_hold") and self._record["state"] == "active"
                and int(self.clock()) < self._record["expires_at"] - MARGIN)

    def inspect_lease(self):
        """Fresh identity/lock proof, NOT permission to authenticate or mutate."""
        if not self.enabled:
            return None
        with self._mutex:
            record=self._observe()
            self._persist()
            if record.get("control_hold"):
                raise QaLeaseClosed("QA control-plane HOLD")
            return dict(record,binding_sha256=binding_sha256(record))

    def admit(self, pull_request=None):
        if not self.enabled:
            return None
        with self._mutex:
            record = self._observe()
            self._persist()
            if not self._is_open() or (pull_request is not None and pull_request not in record["scope"]):
                raise QaLeaseClosed("QA window closed; new work rejected, existing results retained")
            return dict(record,binding_sha256=binding_sha256(record))


    @contextmanager
    def begin(self, operation_id, *, pull_request=None, tenant_id=None,
              message=None, job_id=None, purpose=None, job_metadata=None):
        if not self.enabled:
            yield None
            return
        operation_hash=hashlib.sha256(str(operation_id).encode()).hexdigest()
        ticket=uuid.uuid4().hex
        claim=None; stamp=None
        with self._mutex:
            record=self.admit(pull_request)
            if tenant_id is None and purpose=="auth" and self.kind=="api" and message is None:
                tenant=None  # Exact route plus post-auth membership/token checks belong to API.
            else:
                tenant=self.assert_tenant(record,tenant_id)
            if message is not None:
                stamp=self.validate_message(message,record,tenant_id=tenant,job_id=job_id,job_metadata=job_metadata)
                claim=self._claim_store().claim(stamp)
                self._claim_context[stamp["message_id"]]={"claim":claim,"completed":False}
            self._active[ticket]={"operation_sha256":operation_hash,
                "started_at":int(self.clock()),"lease_revision":record["revision"],
                "tenant_id":tenant,"message_id":stamp["message_id"] if stamp else None}
            try:
                self._persist()
            except Exception:
                self._active.pop(ticket,None)
                # A durable claim may exist. Preserve it as uncertain/in-flight.
                raise QaLeaseClosed("QA activity evidence unavailable") from None
        try:
            yield record
        finally:
            with self._mutex:
                if claim:
                    tracked=self._claim_context.pop(stamp["message_id"])
                    try:
                        self._claim_store().finish(claim,tracked["completed"])
                    except QaLeaseClosed:
                        self.hold_message(message)
                        # Retain active evidence when outcome is unconfirmed.
                        raise
                self._active.pop(ticket,None)
                self._persist()


    def assert_tenant(self, lease, tenant_id):
        try:
            if type(tenant_id) is not int and not (isinstance(tenant_id,str) and re.fullmatch(r"[1-9][0-9]*",tenant_id)):
                raise ValueError("noncanonical tenant")
            tenant = int(tenant_id)
        except (ValueError, TypeError):
            raise QaLeaseClosed("Resolved QA tenant required") from None
        if isinstance(tenant_id, bool) or tenant not in lease.get("tenant_ids", []):
            raise QaLeaseClosed("Tenant is outside this QA lease")
        return tenant

    def _key(self, lease):
        version = lease["message_key_version"]
        if str(version) != self.context.get("ACADEMY_QA_MESSAGE_KEY_VERSION"):
            raise QaLeaseClosed("QA signing key version differs")
        # Explicit context injection is used by offline tests; live values belong
        # to the exact immutable SecureString version for this lease.
        key = self.context.get("ACADEMY_QA_MESSAGE_SIGNING_KEY")
        if key is None:
            try:
                import boto3
                from botocore.config import Config
                ssm = boto3.client("ssm",region_name=REGION,
                    config=Config(connect_timeout=2,read_timeout=2,retries={"max_attempts":1}))
                value = ssm.get_parameter(
                    Name=f"/academy/qa-leases/{lease['lease_id']}/message-signing-key:{version}",
                    WithDecryption=True)["Parameter"]
                if value["Version"] != version or value["Type"] != "SecureString":
                    raise QaLeaseClosed("QA signing key readback differs")
                key = value["Value"]
            except Exception:
                raise QaLeaseClosed("QA signing key unavailable") from None
        if not isinstance(key,str) or not re.fullmatch(r"[0-9a-f]{64}",key):
            raise QaLeaseClosed("QA signing key invalid")
        return bytes.fromhex(key)

    def stamp_message(self, payload, tenant_id, *, job_id, queue_kind=None, job_metadata=None):
        if not self.enabled:
            return payload
        lease = self.admit()
        tenant = self.assert_tenant(lease,tenant_id)
        if not isinstance(payload,dict) or not isinstance(job_id,str) or not job_id.strip():
            raise QaLeaseClosed("Canonical job payload and ID required")
        queue_kind = queue_kind or self.kind
        if queue_kind not in {"ai","tools","messaging"}:
            raise QaLeaseClosed("Exact QA destination worker required")
        if queue_kind in {"ai","tools"}:
            fields={"job_type","tier","source_domain","source_id","created_at","attempt"}
            if (not isinstance(job_metadata,dict) or set(job_metadata)!=fields
                    or not isinstance(job_metadata["job_type"],str) or not job_metadata["job_type"]
                    or not isinstance(job_metadata["tier"],str) or not job_metadata["tier"]
                    or not isinstance(job_metadata["created_at"],str)
                    or type(job_metadata["attempt"]) is not int or job_metadata["attempt"] < 0
                    or any(job_metadata[k] is not None and not isinstance(job_metadata[k],str)
                           for k in ("source_domain","source_id"))):
                raise QaLeaseClosed("Canonical AI/Tools envelope metadata required")
        elif job_metadata is not None:
            raise QaLeaseClosed("Messaging provenance binds its complete body")
        body = copy.deepcopy(payload)
        body.pop("_qa_lease",None)
        stamp = {k:copy.deepcopy(lease[k]) for k in (
            "lease_id","owner_task","lock_owner","revision","source_sha","images","message_key_version")}
        stamp.update(schema_version=1,binding_sha256=binding_sha256(lease), tenant_id=tenant, job_id=str(job_id),
                     job_metadata=copy.deepcopy(job_metadata),
                     queue_kind=queue_kind,message_id=uuid.uuid4().hex,
                     issued_at=int(self.clock()),expires_at=lease["expires_at"],
                     payload_sha256=hashlib.sha256(_canonical(body).encode()).hexdigest())
        stamp["signature"]=hmac.new(self._key(lease),_canonical(stamp).encode(),hashlib.sha256).hexdigest()
        body["_qa_lease"]=stamp
        return body

    def validate_message(self, payload, lease=None, *, tenant_id=None, job_id=None, job_metadata=None):
        if not self.enabled:
            return None
        # Always compare to a fresh committed record, even when caller supplies
        # the lease it observed before long polling.
        current=self.admit()
        if lease is not None and (lease.get("lease_id") != current["lease_id"]
                                  or lease.get("revision") != current["revision"]):
            raise QaLeaseClosed("QA lease changed during receipt delivery")
        lease=current
        if not isinstance(payload,dict) or not isinstance(payload.get("_qa_lease"),dict):
            raise QaLeaseClosed("QA message provenance missing")
        body=copy.deepcopy(payload); stamp=body.pop("_qa_lease")
        signed=dict(stamp); signature=signed.pop("signature",None)
        if not isinstance(signature,str) or not hmac.compare_digest(
                hmac.new(self._key(lease),_canonical(signed).encode(),hashlib.sha256).hexdigest(),signature):
            raise QaLeaseClosed("QA message signature differs")
        for field in ("lease_id","owner_task","lock_owner","revision","source_sha","images","message_key_version"):
            if stamp.get(field) != lease[field]:
                raise QaLeaseClosed("QA message belongs to another lease revision/candidate")
        if (type(stamp.get("schema_version")) is not int or stamp["schema_version"] != 1
                or stamp.get("binding_sha256") != binding_sha256(lease)
                or stamp.get("payload_sha256") != hashlib.sha256(_canonical(body).encode()).hexdigest()
                or stamp.get("queue_kind") != self.kind
                or not re.fullmatch(r"[0-9a-f]{32}",stamp.get("message_id",""))
                or type(stamp.get("issued_at")) is not int
                or not lease["started_at"] <= stamp["issued_at"] <= int(self.clock())
                or stamp.get("expires_at") != lease["expires_at"]
                or int(self.clock()) >= stamp["expires_at"]-MARGIN):
            raise QaLeaseClosed("QA message provenance or freshness differs")
        if self.kind in {"ai","tools"}:
            if not isinstance(job_metadata,dict) or _canonical(stamp.get("job_metadata")) != _canonical(job_metadata):
                raise QaLeaseClosed("QA envelope metadata differs")
        elif job_metadata is not None:
            raise QaLeaseClosed("Unexpected messaging envelope metadata")
        tenant=self.assert_tenant(lease,stamp.get("tenant_id"))
        if tenant_id is None or tenant != self.assert_tenant(lease,tenant_id):
            raise QaLeaseClosed("Authoritative job tenant differs")
        if not isinstance(job_id,str) or stamp.get("job_id") != job_id:
            raise QaLeaseClosed("Authoritative job ID differs")
        return stamp

    def _claim_store(self):
        if self._claims is None:
            self._read()  # Initializes only the scoped QA SDK client.
            self._claims=MessageClaims(self._aws_client)
        return self._claims

    def complete_message(self,payload):
        if not self.enabled:
            return
        message_id=payload.get("_qa_lease",{}).get("message_id")
        with self._mutex:
            if message_id not in self._claim_context:
                raise QaLeaseClosed("No owned QA message claim")
            self._claim_context[message_id]["completed"]=True

    def hold_message(self,payload,reason="qa_message_rejected"):
        if not self.enabled:
            return
        with self._mutex:
            # No body, recipient, raw receipt or exception text enters readback.
            stamp=payload.get("_qa_lease",{}) if isinstance(payload,dict) else {}
            message_id=stamp.get("message_id","") if isinstance(stamp,dict) else ""
            self._holds.append({"message_id":message_id if isinstance(message_id,str) and
                re.fullmatch(r"[0-9a-f]{32}",message_id) else None,
                "reason":"qa_message_rejected","observed_at":int(self.clock())})
            self._persist()
    def snapshot(self):
        if not self.enabled:
            return {"enabled": False, "kind": self.kind}
        with self._mutex:
            return {"enabled": True, "kind": self.kind, "pid": self._pid,
                    "process_id": self._process_id,
                    "observed_at": int(self.clock()), "alive": not self._stop.is_set(),
                    "lease_id": self.context["ACADEMY_QA_LEASE_ID"],
                    "binding_sha256": self.context["ACADEMY_QA_BINDING_SHA256"],
                    "revision": self._record["revision"] if self._record else None,
                    "enforcement_expires_at": self._record["expires_at"] if self._record else None,
                    "lease_verified": self._valid,
                    "control_hold": bool(self._record and self._record.get("control_hold")),
                    "admission": "open" if self._is_open() else "closed",
                    "inflight": list(self._active.values()), "holds": list(self._holds)}

    def _persist(self):
        data = self.snapshot()
        target = self._directory / f"{self.kind}-{self._pid}-{self._process_id}.json"
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, sort_keys=True) + "\n", encoding="utf-8")
        os.chmod(temporary, 0o600)
        temporary.replace(target)

    def _heartbeat(self):
        while not self._stop.is_set():
            with self._mutex:
                try:
                    self._observe()
                except QaLeaseClosed:
                    pass
                try:
                    self._persist()
                except OSError:
                    self._valid = False
            self._stop.wait(2)

    def close(self):
        if not self.enabled:
            return
        self._stop.set()
        with self._mutex:
            try:
                self._persist()
            except OSError:
                pass  # Missing/stale evidence remains a restoration HOLD.


@lru_cache(maxsize=4)
def _process_admission_gate(kind):
    return AdmissionGate(kind)


def get_admission_gate(kind):
    """Reuse one process gate only while its QA identity remains unchanged."""
    gate = _process_admission_gate(kind)
    identity_keys = (
        "CANDIDATE_LEASE_REQUIRED", "ACADEMY_QA_MODE",
        "ACADEMY_QA_LEASE_ID", "ACADEMY_QA_BINDING_SHA256",
        "ACADEMY_RUNTIME_ENV", "ACADEMY_QA_MESSAGE_KEY_VERSION",
    )
    required = os.environ.get("CANDIDATE_LEASE_REQUIRED", "").strip().lower()
    configured = required not in ("", "false", "0") or any(
        os.environ.get(key, "").strip() for key in (
            "ACADEMY_QA_MODE", "ACADEMY_QA_LEASE_ID", "ACADEMY_QA_BINDING_SHA256",
        )
    )
    if (gate.enabled or configured) and any(
        os.environ.get(key, "") != gate.context.get(key, "") for key in identity_keys
    ):
        # Never replace a gate that may own in-flight work or silently reuse a
        # disabled instance after QA configuration appears. Restart is required.
        raise QaLeaseClosed("QA process identity changed; restart with pinned configuration")
    return gate
