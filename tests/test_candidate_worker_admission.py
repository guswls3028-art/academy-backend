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
    ("true", True), ("1", True), ("no", True), ("invalid", True),
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

        def stamp_message(self, payload, tenant_id, *, job_id, queue_kind, job_metadata):
            assert (tenant_id, job_id, queue_kind) == ("17", "job-1", "ai")
            assert job_metadata["job_type"] == "ocr"
            assert job_metadata["attempt"] == 1
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

        def stamp_message(self, payload, tenant_id, *, job_id, queue_kind, job_metadata):
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


@pytest.mark.parametrize("body", [
    "{", '{"payload": {"x": 1}}',
    '{"job_id":"a","job_id":"b"}',
    '{"payload":{"x":1,"x":2}}',
    '{"attempt":NaN}',
])
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


def test_qa_receive_rejects_unknown_top_level(monkeypatch):
    monkeypatch.setattr(sqs_queue, "_candidate_qa_requested", lambda: True)
    queue = _queue_for_unit()
    queue.queue_client.receive_message.return_value = {
        "Body": json.dumps({
            "job_id": "job-1", "job_type": "ocr", "tier": "basic",
            "tenant_id": "17", "source_domain": None, "source_id": None,
            "created_at": "2026-09-26T00:00:00Z", "attempt": 1,
            "payload": {"_qa_lease": {"server": "signed"}},
            "unknown": "do-not-trust",
        }),
        "ReceiptHandle": "rh-1", "MessageId": "sqs-1",
    }

    received = queue.receive_message(tier="basic", wait_time_seconds=1)

    assert received["_qa_malformed"] is True
    queue.queue_client.delete_message.assert_not_called()


@pytest.mark.parametrize("field,value", [
    ("tier", "premium"), ("attempt", True), ("tenant_id", 17),
])
def test_qa_receive_rejects_wrong_route_or_envelope_type(monkeypatch, field, value):
    monkeypatch.setattr(sqs_queue, "_candidate_qa_requested", lambda: True)
    queue = _queue_for_unit()
    body = {
        "job_id": "job-1", "job_type": "ocr", "tier": "basic",
        "payload": {"_qa_lease": {"server": "signed"}}, "tenant_id": "17",
        "source_domain": None, "source_id": None,
        "created_at": "2026-09-26T00:00:00Z", "attempt": 1,
    }
    body[field] = value
    queue.queue_client.receive_message.return_value = {
        "Body": json.dumps(body), "ReceiptHandle": "rh-1", "MessageId": "sqs-1",
    }

    assert queue.receive_message(tier="basic", wait_time_seconds=1)["_qa_malformed"] is True
    queue.queue_client.delete_message.assert_not_called()


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
            raise sys.modules["apps.infrastructure.qa_lease"].QaLeaseClosed("window closed")
        return {"lease": "current"}

    def validate_message(self, payload, lease, *, tenant_id, job_id, job_metadata):
        self.events.append(("validate", tenant_id, job_id, job_metadata))
        return {"tenant_id": int(tenant_id), "job_id": job_id}

    @contextmanager
    def begin(self, operation_id, *, tenant_id, message, job_id, job_metadata):
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
    helper.QaLeaseClosed = type("QaLeaseClosed", (RuntimeError,), {})
    helper.QaMessageInFlight = type("QaMessageInFlight", (helper.QaLeaseClosed,), {})
    helper.QaMessageCompleted = type("QaMessageCompleted", (helper.QaLeaseClosed,), {})
    monkeypatch.setitem(sys.modules, helper.__name__, helper)
    monkeypatch.setattr(ai_sqs_worker.signal, "signal", lambda *args: None)
    monkeypatch.setattr(ai_sqs_worker, "close_old_connections", lambda: None)
    monkeypatch.setattr(ai_sqs_worker, "_release_db_connections", lambda: None)
    monkeypatch.setattr(ai_sqs_worker, "DjangoUnitOfWork", lambda: object())
    monkeypatch.setattr(ai_sqs_worker, "MIN_JOB_INTERVAL_SECONDS", 0)
    monkeypatch.setattr(ai_sqs_worker, "IDLE_SCALE_IN_ENABLED", False)
    monkeypatch.setattr(
        ai_sqs_worker, "_weighted_poll",
        lambda queue, **kwargs: (queue.receive(tier="basic", wait_time_seconds=1), "basic"),
    )
    monkeypatch.setattr(
        ai_sqs_worker.time, "sleep", lambda seconds: setattr(ai_sqs_worker, "_shutdown", True),
    )
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
        "source_domain": None, "source_id": None,
        "created_at": "2026-09-26T00:00:00Z", "attempt": 1,
    }


def _qa_message_metadata_for_test():
    return {key: _message()[key] for key in (
        "job_type", "tier", "source_domain", "source_id", "created_at", "attempt",
    )}


def _real_gate_config(qa_lease):
    record = {
        "lease_id": "a" * 32, "owner_task": "01a0d04a-d64f-7473-9f58-61a8e983dcc0",
        "lock_owner": "candidate:123:1", "source_sha": "b" * 40,
        "images": {kind: "sha256:" + "c" * 64 for kind in (
            "api", "ai", "tools", "messaging",
        )},
        "endpoint": "ssm://i-0123456789abcdef0:8001",
        "profile": "arn:aws:iam::809466760795:instance-profile/academy-api-qa",
        "scope": [509, 511], "baseline_sha256": "d" * 64,
        "tenant_ids": [17], "message_key_version": 1, "resource_manifest_sha256": "e"*64,
        "state": "active", "revision": 1,
        "started_at": 900, "renewed_at": 900, "expires_at": 1600,
    }
    context = {
        "ACADEMY_RUNTIME_ENV": "development", "ACADEMY_QA_MODE": "isolated-qa",
        "CANDIDATE_LEASE_REQUIRED": "true",
        "ACADEMY_QA_LEASE_ID": "a" * 32,
        "ACADEMY_QA_BINDING_SHA256": qa_lease.binding_sha256(record),
        "ACADEMY_QA_MESSAGE_KEY_VERSION": "1",
        "ACADEMY_QA_MESSAGE_SIGNING_KEY": "e" * 64,
    }
    return record, context


@pytest.mark.parametrize("mismatch", ["tenant", "payload", "tier", "source"])
def test_qa_db_receipt_boundary_rejects_mismatch_after_claim(monkeypatch, mismatch):
    from apps.domains.ai.models import AIJobModel

    receipt = _message()
    job = SimpleNamespace(
        job_id="job-1", tenant_id="17", job_type="ocr", tier="basic",
        source_domain=None, source_id=None, payload=_message()["payload"], status="PENDING",
    )
    if mismatch == "tenant":
        job.tenant_id = "18"
    elif mismatch == "payload":
        receipt["payload"] = {"_qa_lease": "tampered"}
    elif mismatch == "tier":
        receipt["tier"] = "premium"
    else:
        receipt["source_domain"] = "other"

    class Manager:
        filter_calls = 0

        def filter(self, **kwargs):
            self.filter_calls += 1
            self.requested_tenant = kwargs["tenant_id"]
            return self

        def only(self, *fields):
            return self

        def first(self):
            return job if self.requested_tenant == "17" else None

    manager = Manager()
    monkeypatch.setattr(AIJobModel, "objects", manager)
    with pytest.raises(ValueError):
        ai_sqs_worker._qa_job_for_receipt(receipt, {"tenant_id": 17, "job_id": "job-1"})
    assert manager.filter_calls == 1


def test_qa_worker_closes_before_poll(worker_units):
    queue = _Queue(_message())
    gate = _Gate(close_at_admit=1)

    assert ai_sqs_worker.run_ai_sqs_worker(queue=queue, admission_gate=gate) == 0
    assert queue.received == 0
    assert queue.deleted == []


@pytest.mark.parametrize("initial_state,expected_receives", [
    ("prepared", 2), ("draining", 0), ("expired", 0),
])
def test_real_gate_keeps_worker_and_heartbeat_alive_while_closed(
    monkeypatch, tmp_path, initial_state, expected_receives,
):
    from apps.infrastructure import qa_lease

    record, context = _real_gate_config(qa_lease)
    record["state"] = "active" if initial_state == "expired" else initial_state
    reader = lambda: (dict(record), {"owner": record["lock_owner"], "expires_at": 2000})
    gate = qa_lease.AdmissionGate(
        "ai", context=context, reader=reader,
        clock=lambda: 1600 if initial_state == "expired" else 1000,
        activity_dir=tmp_path, heartbeat=True,
    )
    queue = _Queue(None)
    db_close = Mock()
    db_release = Mock()
    monkeypatch.setattr(ai_sqs_worker.signal, "signal", lambda *args: None)
    monkeypatch.setattr(ai_sqs_worker, "close_old_connections", db_close)
    monkeypatch.setattr(ai_sqs_worker, "_release_db_connections", db_release)
    monkeypatch.setattr(ai_sqs_worker, "IDLE_SCALE_IN_ENABLED", False)
    monkeypatch.setattr(
        ai_sqs_worker, "_weighted_poll",
        lambda queue, **kwargs: (queue.receive(tier="basic", wait_time_seconds=1), "basic"),
    )
    monkeypatch.setattr(
        ai_sqs_worker, "SQSVisibilityExtender",
        lambda queue: SimpleNamespace(start=lambda **kwargs: None, stop=lambda: None),
    )

    def on_pause(_seconds):
        assert gate._thread.is_alive()
        assert gate.snapshot()["admission"] == "closed"
        assert queue.received == 0
        db_close.assert_not_called()
        db_release.assert_not_called()
        if initial_state == "prepared":
            record["state"] = "active"
        else:
            ai_sqs_worker._shutdown = True

    monkeypatch.setattr(ai_sqs_worker.time, "sleep", on_pause)
    ai_sqs_worker._shutdown = False
    try:
        assert ai_sqs_worker.run_ai_sqs_worker(queue=queue, admission_gate=gate) == 0
        assert queue.received == expected_receives
        assert gate.snapshot()["alive"] is True
    finally:
        ai_sqs_worker._shutdown = False
        gate.close()


def test_qa_worker_releases_receipt_if_window_closes_during_longpoll(worker_units):
    queue = _Queue(_message())
    gate = _Gate(close_at_admit=2)

    assert ai_sqs_worker.run_ai_sqs_worker(queue=queue, admission_gate=gate) == 0
    assert queue.released == [("rh-1", "basic")]
    assert queue.deleted == []
    assert ("hold", "qa_after_poll_closed") in gate.events


@pytest.mark.parametrize("tamper", ["tenant", "job_id", "tier", "attempt", "created_at"])
def test_forged_signed_receipt_never_queries_job_or_claims_nonce(
    monkeypatch, tmp_path, tamper,
):
    from apps.domains.ai.models import AIJobModel
    from apps.infrastructure import qa_lease

    record, context = _real_gate_config(qa_lease)
    claims = Mock()
    gate = qa_lease.AdmissionGate(
        "ai", context=context,
        reader=lambda: (dict(record), {"owner": record["lock_owner"], "expires_at": 2000}),
        clock=lambda: 1000, activity_dir=tmp_path, heartbeat=False, claims=claims,
    )
    metadata = _qa_message_metadata_for_test()
    payload = gate.stamp_message(
        {"input": "safe"}, "17", job_id="job-1", queue_kind="ai", job_metadata=metadata,
    )
    message = {**_message(), **metadata, "payload": payload}
    message[tamper] = {
        "tenant": "18", "job_id": "job-2", "tier": "premium", "attempt": 2,
        "created_at": "2026-09-27T00:00:00Z",
    }[tamper]
    if tamper == "tenant":
        message["tenant_id"] = message.pop("tenant")
    queue = _Queue(message)
    manager = Mock()
    monkeypatch.setattr(AIJobModel, "objects", manager)
    prepare = Mock()
    monkeypatch.setattr(ai_sqs_worker, "prepare_ai_job", prepare)
    monkeypatch.setattr(ai_sqs_worker.signal, "signal", lambda *args: None)
    monkeypatch.setattr(ai_sqs_worker, "close_old_connections", lambda: None)
    monkeypatch.setattr(ai_sqs_worker, "_release_db_connections", lambda: None)
    monkeypatch.setattr(ai_sqs_worker, "IDLE_SCALE_IN_ENABLED", False)
    monkeypatch.setattr(
        ai_sqs_worker, "_weighted_poll",
        lambda queue, **kwargs: (queue.receive(tier="basic", wait_time_seconds=1), "basic"),
    )
    monkeypatch.setattr(
        ai_sqs_worker, "SQSVisibilityExtender",
        lambda queue: SimpleNamespace(start=lambda **kwargs: None, stop=lambda: None),
    )
    monkeypatch.setattr(
        ai_sqs_worker.time, "sleep", lambda seconds: setattr(ai_sqs_worker, "_shutdown", True),
    )
    ai_sqs_worker._shutdown = False
    try:
        assert ai_sqs_worker.run_ai_sqs_worker(queue=queue, admission_gate=gate) == 0
        manager.filter.assert_not_called()
        claims.claim.assert_not_called()
        prepare.assert_not_called()
        assert queue.released == [("rh-1", "basic")]
        assert queue.deleted == []
        assert gate.snapshot()["holds"]
    finally:
        ai_sqs_worker._shutdown = False
        gate.close()


def test_qa_worker_rejects_stale_or_other_tenant_before_prepare(worker_units, monkeypatch):
    queue = _Queue(_message())
    gate = _Gate()
    monkeypatch.setattr(
        ai_sqs_worker, "_qa_job_for_receipt",
        lambda *args: (_ for _ in ()).throw(ValueError("wrong tenant")),
    )
    prepare = Mock()
    monkeypatch.setattr(ai_sqs_worker, "prepare_ai_job", prepare)

    assert ai_sqs_worker.run_ai_sqs_worker(queue=queue, admission_gate=gate) == 0
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

    assert ai_sqs_worker.run_ai_sqs_worker(queue=queue, admission_gate=gate) == 0
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

    record, context = _real_gate_config(qa_lease)
    events = []

    class Claims:
        state = None

        def claim(self, stamp):
            events.append("claim")
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
                events.append("orm")
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
        monkeypatch.setattr(
            ai_sqs_worker, "_weighted_poll",
            lambda queue, **kwargs: (queue.receive(tier="basic", wait_time_seconds=1), "basic"),
        )
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
        assert events.index("claim") < events.index("orm")
        assert gates["ai"].snapshot()["inflight"] == []
    finally:
        ai_sqs_worker._shutdown = False
        for gate in gates.values():
            gate.close()
