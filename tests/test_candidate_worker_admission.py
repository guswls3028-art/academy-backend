"""Candidate QA receipts fail closed without changing ordinary SQS workers."""

from __future__ import annotations

import sys
import types
import json
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from academy.framework.workers import ai_sqs_worker
from apps.support.ai.services import sqs_queue


class _Job:
    status = "PENDING"
    job_id = "job-1"
    job_type = "ocr"
    tier = "basic"
    tenant_id = "17"
    source_domain = None
    source_id = None

    def __init__(self, events):
        self.events = events
        self.payload = {"_qa_lease": {"forged": True}, "data": 1}

    def save(self, *, update_fields):
        assert update_fields == ["payload"]
        self.events.append("persist")


def _queue_for_unit():
    queue = sqs_queue.AISQSQueue.__new__(sqs_queue.AISQSQueue)
    queue.queue_name_override = None
    queue.wake_ai_workers = False
    queue.queue_client = Mock()
    queue._get_queue_name = lambda tier: "qa-ai"
    return queue


@pytest.mark.parametrize("required,requested", [
    ("", False), ("false", False), ("0", False),
    ("true", True), ("1", True), ("invalid", True),
])
def test_required_lease_flag_never_falls_through_to_ordinary_queue(
    monkeypatch, required, requested,
):
    for name in ("ACADEMY_QA_MODE", "ACADEMY_QA_LEASE_ID", "ACADEMY_QA_BINDING_SHA256"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("CANDIDATE_LEASE_REQUIRED", required)

    assert ai_sqs_worker._qa_gate_requested() is requested
    assert sqs_queue._candidate_qa_requested() is requested


def test_required_lease_without_active_gate_stops_before_receive(monkeypatch):
    monkeypatch.setenv("CANDIDATE_LEASE_REQUIRED", "true")
    for name in ("ACADEMY_QA_MODE", "ACADEMY_QA_LEASE_ID", "ACADEMY_QA_BINDING_SHA256"):
        monkeypatch.delenv(name, raising=False)
    helper = types.ModuleType("apps.infrastructure.qa_lease")
    helper.get_admission_gate = lambda kind: SimpleNamespace(enabled=False)
    monkeypatch.setitem(sys.modules, helper.__name__, helper)

    with pytest.raises(RuntimeError, match="not available"):
        ai_sqs_worker._worker_admission_gate("ai")


def test_qa_enqueue_overwrites_client_stamp_and_persists_before_send(monkeypatch):
    events = []

    class Gate:
        enabled = True

        def __init__(self, *, kind):
            assert kind == "api"

        def admit(self):
            events.append("admit")

        def stamp_message(self, payload, tenant_id, *, job_id, queue_kind):
            assert (tenant_id, job_id, queue_kind) == ("17", "job-1", "ai")
            assert payload["_qa_lease"] == {"forged": True}
            return {"data": payload["data"], "_qa_lease": {"server": "signed"}}

    helper = types.ModuleType("apps.infrastructure.qa_lease")
    helper.get_admission_gate = lambda kind: Gate(kind=kind)
    monkeypatch.setitem(sys.modules, helper.__name__, helper)
    monkeypatch.setattr(sqs_queue, "_candidate_qa_requested", lambda: True)
    queue = _queue_for_unit()
    queue.queue_client.send_message.side_effect = lambda **kwargs: events.append("send") or True
    job = _Job(events)

    assert queue.enqueue(job) is True
    assert events == ["admit", "persist", "send"]
    assert job.payload["_qa_lease"] == {"server": "signed"}
    sent = queue.queue_client.send_message.call_args.kwargs["message"]
    assert sent["payload"] == job.payload
    assert sent["tenant_id"] == "17"


def test_qa_enqueue_missing_signer_never_sends(monkeypatch):
    class Gate:
        enabled = True

        def __init__(self, *, kind):
            pass

        def admit(self):
            pass

        def stamp_message(self, payload, tenant_id, *, job_id, queue_kind):
            raise RuntimeError("signer unavailable")

    helper = types.ModuleType("apps.infrastructure.qa_lease")
    helper.get_admission_gate = lambda kind: Gate(kind=kind)
    monkeypatch.setitem(sys.modules, helper.__name__, helper)
    monkeypatch.setattr(sqs_queue, "_candidate_qa_requested", lambda: True)
    queue = _queue_for_unit()
    job = _Job([])

    with pytest.raises(RuntimeError, match="signer unavailable"):
        queue.enqueue(job)
    queue.queue_client.send_message.assert_not_called()
    assert job.payload["_qa_lease"] == {"forged": True}


def test_qa_enqueue_without_authoritative_tenant_never_sends(monkeypatch):
    monkeypatch.setattr(sqs_queue, "_candidate_qa_requested", lambda: True)
    queue = _queue_for_unit()
    job = _Job([])
    job.tenant_id = None

    with pytest.raises(ValueError, match="requires a tenant"):
        queue.enqueue(job)
    queue.queue_client.send_message.assert_not_called()


@pytest.mark.parametrize("body", ["{", '{"payload": {"x": 1}}'])
def test_qa_receive_preserves_untrusted_receipt_for_worker_rejection(monkeypatch, body):
    monkeypatch.setattr(sqs_queue, "_candidate_qa_requested", lambda: True)
    queue = _queue_for_unit()
    queue.queue_client.receive_message.return_value = {
        "Body": body, "ReceiptHandle": "rh-1", "MessageId": "sqs-1",
    }

    received = queue.receive_message(tier="basic", wait_time_seconds=1)

    assert received == {
        "receipt_handle": "rh-1", "tier": "basic", "payload": {}, "_qa_malformed": True,
    }
    queue.queue_client.delete_message.assert_not_called()


def test_qa_receive_preserves_payload_stamp_and_drops_unknown_top_level(monkeypatch):
    monkeypatch.setattr(sqs_queue, "_candidate_qa_requested", lambda: True)
    queue = _queue_for_unit()
    queue.queue_client.receive_message.return_value = {
        "Body": {
            "job_id": "job-1", "job_type": "ocr", "tier": "basic",
            "payload": {"_qa_lease": {"server": "signed"}},
            "unknown": "do-not-trust",
        },
        "ReceiptHandle": "rh-1", "MessageId": "sqs-1",
    }

    received = queue.receive_message(tier="basic", wait_time_seconds=1)

    assert received["payload"]["_qa_lease"] == {"server": "signed"}
    assert "unknown" not in received


class _Gate:
    enabled = True

    def __init__(self, *, close_at_admit=None, outcome=None):
        self.close_at_admit = close_at_admit
        self.outcome = outcome
        self.admit_count = 0
        self.events = []

    def admit(self):
        self.admit_count += 1
        if self.admit_count == self.close_at_admit:
            raise RuntimeError("window closed")
        return {"lease": "current"}

    @contextmanager
    def begin(self, operation_id, *, tenant_id, message, job_id):
        self.events.append(("begin", operation_id, tenant_id, job_id))
        if self.outcome:
            raise self.outcome()
        try:
            yield
        finally:
            self.events.append(("end", operation_id))

    def hold_message(self, payload, *, reason):
        self.events.append(("hold", reason))

    def complete_message(self, payload):
        self.events.append(("complete", payload["_qa_lease"]))


class _Queue:
    def __init__(self, message=None):
        self.message = message
        self.received = 0
        self.released = []
        self.deleted = []
        self.deferred = []

    def receive(self, *, tier, wait_time_seconds):
        self.received += 1
        if self.received > 1:
            ai_sqs_worker._shutdown = True
            return None
        return self.message

    def release_unstarted(self, receipt_handle, tier):
        self.released.append((receipt_handle, tier))
        return True

    def delete(self, receipt_handle, tier):
        self.deleted.append((receipt_handle, tier))
        return True

    def extend_visibility(self, receipt_handle, tier, seconds):
        self.deferred.append((receipt_handle, tier, seconds))
        return True


@pytest.fixture
def worker_units(monkeypatch):
    helper = types.ModuleType("apps.infrastructure.qa_lease")
    helper.QaMessageInFlight = type("QaMessageInFlight", (Exception,), {})
    helper.QaMessageCompleted = type("QaMessageCompleted", (Exception,), {})
    monkeypatch.setitem(sys.modules, helper.__name__, helper)
    monkeypatch.setattr(ai_sqs_worker.signal, "signal", lambda *args: None)
    monkeypatch.setattr(ai_sqs_worker, "close_old_connections", lambda: None)
    monkeypatch.setattr(ai_sqs_worker, "_release_db_connections", lambda: None)
    monkeypatch.setattr(ai_sqs_worker, "DjangoUnitOfWork", lambda: object())
    monkeypatch.setattr(ai_sqs_worker, "MIN_JOB_INTERVAL_SECONDS", 0)
    monkeypatch.setattr(ai_sqs_worker, "IDLE_SCALE_IN_ENABLED", False)
    monkeypatch.setattr(
        ai_sqs_worker, "SQSVisibilityExtender",
        lambda queue: SimpleNamespace(start=lambda **kwargs: None, stop=lambda: None),
    )
    heartbeat = types.ModuleType("apps.shared.utils.heartbeat")
    heartbeat.beat = lambda *args: None
    monkeypatch.setitem(sys.modules, heartbeat.__name__, heartbeat)
    ai_sqs_worker._shutdown = False
    yield helper
    ai_sqs_worker._shutdown = False


def _message():
    return {
        "job_id": "job-1", "receipt_handle": "rh-1", "job_type": "ocr",
        "tier": "basic", "tenant_id": "17", "payload": {"_qa_lease": "signed"},
    }


@pytest.mark.parametrize("mismatch", ["tenant", "payload", "tier", "source"])
def test_qa_db_receipt_boundary_rejects_mismatch_before_claim(monkeypatch, mismatch):
    from apps.domains.ai.models import AIJobModel

    receipt = _message()
    job = SimpleNamespace(
        job_id="job-1", tenant_id="17", job_type="ocr", tier="basic",
        source_domain=None, source_id=None, payload=_message()["payload"], status="PENDING",
    )
    if mismatch == "tenant":
        receipt["tenant_id"] = "18"
    elif mismatch == "payload":
        receipt["payload"] = {"_qa_lease": "tampered"}
    elif mismatch == "tier":
        receipt["tier"] = "premium"
    else:
        receipt["source_domain"] = "other"

    class Manager:
        def filter(self, **kwargs):
            self.requested_tenant = kwargs["tenant_id"]
            return self

        def only(self, *fields):
            return self

        def first(self):
            return job if self.requested_tenant == "17" else None

    monkeypatch.setattr(AIJobModel, "objects", Manager())
    gate = Mock()

    with pytest.raises(ValueError):
        ai_sqs_worker._qa_job_for_receipt(receipt, gate, {"lease": "current"})
    gate.validate_message.assert_not_called()


def test_qa_worker_closes_before_poll(worker_units):
    queue = _Queue(_message())
    gate = _Gate(close_at_admit=1)

    assert ai_sqs_worker.run_ai_sqs_worker(queue=queue, admission_gate=gate) == 1
    assert queue.received == 0
    assert queue.deleted == []


def test_qa_worker_releases_receipt_if_window_closes_during_longpoll(worker_units):
    queue = _Queue(_message())
    gate = _Gate(close_at_admit=2)

    assert ai_sqs_worker.run_ai_sqs_worker(queue=queue, admission_gate=gate) == 1
    assert queue.released == [("rh-1", "basic")]
    assert queue.deleted == []
    assert ("hold", "qa_after_poll_closed") in gate.events


def test_qa_worker_rejects_stale_or_other_tenant_before_prepare(worker_units, monkeypatch):
    queue = _Queue(_message())
    gate = _Gate()
    monkeypatch.setattr(
        ai_sqs_worker, "_qa_job_for_receipt",
        lambda *args: (_ for _ in ()).throw(ValueError("wrong tenant")),
    )
    prepare = Mock()
    monkeypatch.setattr(ai_sqs_worker, "prepare_ai_job", prepare)

    assert ai_sqs_worker.run_ai_sqs_worker(queue=queue, admission_gate=gate) == 1
    prepare.assert_not_called()
    assert queue.released == [("rh-1", "basic")]
    assert queue.deleted == []
    assert ("hold", "qa_message_rejected") in gate.events


@pytest.mark.parametrize("outcome,expected", [
    ("QaMessageInFlight", "defer"), ("QaMessageCompleted", "ack"),
])
def test_qa_worker_handles_exact_duplicate_without_inference(
    worker_units, monkeypatch, outcome, expected,
):
    queue = _Queue(_message())
    gate = _Gate(outcome=getattr(worker_units, outcome))
    monkeypatch.setattr(
        ai_sqs_worker, "_qa_job_for_receipt",
        lambda *args: SimpleNamespace(
            tenant_id="17", payload=_message()["payload"], status="DONE",
        ),
    )
    prepare = Mock()
    monkeypatch.setattr(ai_sqs_worker, "prepare_ai_job", prepare)

    assert ai_sqs_worker.run_ai_sqs_worker(queue=queue, admission_gate=gate) == 0
    prepare.assert_not_called()
    if expected == "defer":
        assert queue.deferred == [("rh-1", "basic", 30)]
        assert queue.deleted == []
    else:
        assert queue.deleted == [("rh-1", "basic")]
        assert queue.deferred == []


def test_qa_completed_nonce_cannot_ack_nonterminal_job(worker_units, monkeypatch):
    queue = _Queue(_message())
    gate = _Gate(outcome=worker_units.QaMessageCompleted)
    monkeypatch.setattr(
        ai_sqs_worker, "_qa_job_for_receipt",
        lambda *args: SimpleNamespace(
            tenant_id="17", payload=_message()["payload"], status="PENDING",
        ),
    )

    assert ai_sqs_worker.run_ai_sqs_worker(queue=queue, admission_gate=gate) == 1
    assert queue.deleted == []
    assert queue.released == [("rh-1", "basic")]
    assert ("hold", "qa_completed_disposition_without_terminal_job") in gate.events


def test_qa_prepared_none_with_running_job_keeps_receipt_retryable(worker_units, monkeypatch):
    queue = _Queue(_message())
    gate = _Gate()
    monkeypatch.setattr(
        ai_sqs_worker, "_qa_job_for_receipt",
        lambda *args: SimpleNamespace(
            tenant_id="17", payload=_message()["payload"], status="RUNNING",
        ),
    )
    monkeypatch.setattr(ai_sqs_worker, "prepare_ai_job", lambda *args, **kwargs: None)
    monkeypatch.setattr(ai_sqs_worker, "_qa_job_status", lambda *args: "RUNNING")
    callback = Mock()
    monkeypatch.setattr(ai_sqs_worker, "_dispatch_terminal_callback_from_message", callback)

    assert ai_sqs_worker.run_ai_sqs_worker(queue=queue, admission_gate=gate) == 0
    assert queue.deferred == [("rh-1", "basic", 30)]
    assert queue.deleted == []
    callback.assert_not_called()
    assert ("complete", "signed") not in gate.events


def test_qa_stamp_is_not_sent_into_inference_payload(monkeypatch):
    monkeypatch.setattr(ai_sqs_worker, "_qa_gate_requested", lambda: True)
    from apps.shared.contracts import ai_job

    monkeypatch.setattr(ai_job.AIJob, "from_dict", staticmethod(lambda value: value))
    prepared = SimpleNamespace(
        job_id="job-1", job_type="ocr", tier="basic", tenant_id="17",
        source_domain=None, source_id=None,
        payload={"input": "safe", "_qa_lease": {"server": "signed"}},
    )

    contract = ai_sqs_worker._to_contract_job(prepared)

    assert contract["payload"] == {"input": "safe"}
    assert prepared.payload["_qa_lease"] == {"server": "signed"}


@pytest.mark.parametrize("callback_ok", [True, False])
def test_qa_worker_marks_completion_only_after_callback(
    worker_units, monkeypatch, callback_ok,
):
    queue = _Queue(_message())
    gate = _Gate()
    monkeypatch.setattr(
        ai_sqs_worker, "_qa_job_for_receipt",
        lambda *args: SimpleNamespace(tenant_id="17", payload=_message()["payload"]),
    )
    monkeypatch.setattr(
        ai_sqs_worker, "prepare_ai_job",
        lambda *args, **kwargs: SimpleNamespace(job_id="job-1"),
    )
    monkeypatch.setattr(
        ai_sqs_worker, "_run_inference",
        lambda *args, **kwargs: SimpleNamespace(status="DONE", result={"ok": True}, error=None),
    )
    monkeypatch.setattr(ai_sqs_worker, "complete_ai_job", lambda *args: True)
    monkeypatch.setattr(ai_sqs_worker, "_cleanup_terminal_artifacts", lambda *args: None)
    monkeypatch.setattr(
        ai_sqs_worker, "_dispatch_domain_callback", lambda *args, **kwargs: callback_ok,
    )

    assert ai_sqs_worker.run_ai_sqs_worker(queue=queue, admission_gate=gate) == 0
    assert ("begin", "job-1:rh-1", "17", "job-1") in gate.events
    assert ("end", "job-1:rh-1") in gate.events
    assert (("complete", "signed") in gate.events) is callback_ok
    assert (queue.deleted == [("rh-1", "basic")]) is callback_ok


def test_signed_enqueue_to_worker_completion_with_real_gate(monkeypatch, tmp_path):
    """Exercise the actual signer, parser, DB comparison and nonce disposition."""
    from apps.domains.ai.models import AIJobModel
    from apps.infrastructure import qa_lease
    from academy.adapters.queue.sqs.ai_queue import SQSAIQueueAdapter

    record = {
        "lease_id": "a" * 32, "owner_task": "01a0d04a-d64f-7473-9f58-61a8e983dcc0",
        "lock_owner": "candidate:123:1", "source_sha": "b" * 40,
        "images": {kind: "sha256:" + "c" * 64 for kind in (
            "api", "ai", "tools", "messaging",
        )},
        "endpoint": "ssm://i-0123456789abcdef0:8001",
        "profile": "arn:aws:iam::809466760795:instance-profile/academy-api-qa",
        "scope": [509, 511], "baseline_sha256": "d" * 64,
        "tenant_ids": [17], "message_key_version": 1,
        "state": "active", "revision": 1,
        "started_at": 900, "renewed_at": 900, "expires_at": 1600,
    }
    context = {
        "ACADEMY_RUNTIME_ENV": "development", "ACADEMY_QA_MODE": "isolated-qa",
        "ACADEMY_QA_LEASE_ID": "a" * 32,
        "ACADEMY_QA_BINDING_SHA256": qa_lease.binding_sha256(record),
        "ACADEMY_QA_MESSAGE_KEY_VERSION": "1",
        "ACADEMY_QA_MESSAGE_SIGNING_KEY": "e" * 64,
    }

    class Claims:
        state = None

        def claim(self, stamp):
            self.state = "inflight"
            return {"message_id": stamp["message_id"]}

        def finish(self, claim, completed):
            self.state = "completed" if completed else "retryable"

    claims = Claims()
    reader = lambda: (dict(record), {"owner": "candidate:123:1", "expires_at": 2000})
    gates = {
        kind: qa_lease.AdmissionGate(
            kind, context=context, reader=reader, clock=lambda: 1000,
            activity_dir=tmp_path / kind, heartbeat=False, claims=claims,
        )
        for kind in ("api", "ai")
    }
    try:
        monkeypatch.setattr(qa_lease, "get_admission_gate", lambda kind: gates[kind])
        monkeypatch.setattr(sqs_queue, "_candidate_qa_requested", lambda: True)
        monkeypatch.setattr(ai_sqs_worker, "_qa_gate_requested", lambda: True)

        class Job(_Job):
            status = "PENDING"

        job = Job([])

        class Manager:
            def filter(self, **kwargs):
                assert kwargs == {"job_id": "job-1", "tenant_id": "17"}
                return self

            def only(self, *fields):
                assert "payload" in fields and "status" in fields
                return self

            def first(self):
                return job

        monkeypatch.setattr(AIJobModel, "objects", Manager())

        class Client:
            sent = None
            deleted = False
            receives = 0

            def send_message(self, *, queue_name, message):
                self.sent = message
                return True

            def receive_message(self, *, queue_name, wait_time_seconds):
                self.receives += 1
                if self.receives > 1:
                    ai_sqs_worker._shutdown = True
                    return None
                return {
                    "Body": json.dumps(self.sent), "ReceiptHandle": "rh-1",
                    "MessageId": "sqs-message-1",
                }

            def delete_message(self, *, queue_name, receipt_handle):
                assert claims.state == "completed"
                self.deleted = receipt_handle == "rh-1"
                return self.deleted

        client = Client()
        producer = sqs_queue.AISQSQueue.__new__(sqs_queue.AISQSQueue)
        producer.queue_name_override = None
        producer.wake_ai_workers = False
        producer.queue_client = client
        producer._get_queue_name = lambda tier: "qa-ai"
        assert producer.enqueue(job) is True
        assert client.sent["payload"] == job.payload
        assert job.payload["_qa_lease"]["queue_kind"] == "ai"

        adapter = SQSAIQueueAdapter()
        adapter._impl = producer
        monkeypatch.setattr(ai_sqs_worker.signal, "signal", lambda *args: None)
        monkeypatch.setattr(ai_sqs_worker, "close_old_connections", lambda: None)
        monkeypatch.setattr(ai_sqs_worker, "_release_db_connections", lambda: None)
        monkeypatch.setattr(ai_sqs_worker, "DjangoUnitOfWork", lambda: object())
        monkeypatch.setattr(ai_sqs_worker, "MIN_JOB_INTERVAL_SECONDS", 0)
        monkeypatch.setattr(ai_sqs_worker, "IDLE_SCALE_IN_ENABLED", False)
        monkeypatch.setattr(
            ai_sqs_worker, "SQSVisibilityExtender",
            lambda queue: SimpleNamespace(start=lambda **kwargs: None, stop=lambda: None),
        )
        monkeypatch.setattr(
            ai_sqs_worker, "prepare_ai_job",
            lambda *args, **kwargs: SimpleNamespace(job_id="job-1"),
        )
        monkeypatch.setattr(
            ai_sqs_worker, "_run_inference",
            lambda *args, **kwargs: SimpleNamespace(status="DONE", result={"ok": True}, error=None),
        )
        monkeypatch.setattr(
            ai_sqs_worker, "complete_ai_job",
            lambda *args: setattr(job, "status", "DONE") or True,
        )
        monkeypatch.setattr(ai_sqs_worker, "_cleanup_terminal_artifacts", lambda *args: None)
        monkeypatch.setattr(ai_sqs_worker, "_dispatch_domain_callback", lambda *args, **kwargs: True)
        heartbeat = types.ModuleType("apps.shared.utils.heartbeat")
        heartbeat.beat = lambda *args: None
        monkeypatch.setitem(sys.modules, heartbeat.__name__, heartbeat)
        ai_sqs_worker._shutdown = False

        assert ai_sqs_worker.run_ai_sqs_worker(
            queue=adapter, admission_gate=gates["ai"],
        ) == 0
        assert client.deleted is True
        assert claims.state == "completed"
        assert gates["ai"].snapshot()["inflight"] == []
    finally:
        ai_sqs_worker._shutdown = False
        for gate in gates.values():
            gate.close()
