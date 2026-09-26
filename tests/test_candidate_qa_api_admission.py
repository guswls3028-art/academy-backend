"""QA API admission fails closed without breaking completed-result reads."""

from contextlib import contextmanager
import asyncio
import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from django.http import JsonResponse, StreamingHttpResponse
from django.test import RequestFactory

from apps.api.common import middleware as common_middleware
from apps.api.middleware import candidate_qa_admission as admission


@pytest.fixture
def qa_env(monkeypatch):
    monkeypatch.setenv("CANDIDATE_LEASE_REQUIRED", "true")
    monkeypatch.setenv("ACADEMY_RUNTIME_ENV", "development")
    monkeypatch.setenv("ACADEMY_QA_MODE", "isolated-qa")
    monkeypatch.setenv("ACADEMY_QA_LEASE_ID", "a" * 32)
    monkeypatch.setenv("ACADEMY_QA_BINDING_SHA256", "b" * 64)


class Gate:
    def __init__(self, *, closed=False):
        self.closed = closed
        self.calls = []
        self.active = 0

    def clock(self):
        return time.time()

    def inspect_lease(self):
        if self.closed:
            raise RuntimeError("QA lease unavailable")
        return {"tenant_ids": [7], "state": "active", "expires_at": int(time.time()) + 300}

    def assert_tenant(self, lease, tenant_id):
        if tenant_id not in lease["tenant_ids"]:
            raise RuntimeError("foreign QA tenant")
        return tenant_id

    @contextmanager
    def begin(self, operation_id, *, tenant_id=None, purpose=None):
        self.calls.append((operation_id, tenant_id, purpose))
        if self.closed:
            raise RuntimeError("expired, replayed, mismatched, or adapter unavailable")
        self.active += 1
        try:
            yield {"lease_id": "a" * 32, "revision": 2}
        finally:
            self.active -= 1


def _request(method="post", path="/api/v1/exams/", *, tenant_id=7):
    request = getattr(RequestFactory(), method)(path)
    request.tenant = SimpleNamespace(pk=tenant_id) if tenant_id is not None else None
    return request


def test_no_qa_configuration_keeps_normal_development_mutation(monkeypatch):
    for key in admission._QA_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("CANDIDATE_LEASE_REQUIRED", "false")
    monkeypatch.setattr(admission, "_gate", lambda: pytest.fail("gate must stay inactive"))
    response = admission.CandidateQaAdmissionMiddleware(
        lambda request: JsonResponse({"ok": True})
    )(_request())
    assert response.status_code == 200


@pytest.mark.parametrize(
    ("marker", "value"),
    [("ACADEMY_QA_MODE", "isolated-qa"), ("CANDIDATE_LEASE_REQUIRED", "true")],
)
def test_partial_qa_configuration_and_missing_adapter_fail_closed(monkeypatch, marker, value):
    for key in admission._QA_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv("CANDIDATE_LEASE_REQUIRED", raising=False)
    monkeypatch.setenv("ACADEMY_RUNTIME_ENV", "development")
    monkeypatch.setenv(marker, value)
    monkeypatch.setattr(admission, "_gate", lambda: Gate(closed=True))
    called = []
    response = admission.CandidateQaAdmissionMiddleware(
        lambda request: called.append(request) or JsonResponse({"ok": True})
    )(_request())
    assert response.status_code == 503
    assert json.loads(response.content)["code"] == "QA_WINDOW_CLOSED"
    assert response["Cache-Control"] == "no-store"
    assert not called


@pytest.mark.parametrize(
    ("marker", "value"),
    [("CANDIDATE_LEASE_REQUIRED", "true"), ("ACADEMY_QA_MODE", "isolated-qa")],
)
def test_incomplete_candidate_identity_fails_worker_startup(monkeypatch, marker, value):
    from apps.infrastructure.qa_lease import QaLeaseClosed

    for key in admission._QA_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv("CANDIDATE_LEASE_REQUIRED", raising=False)
    monkeypatch.setenv("ACADEMY_RUNTIME_ENV", "development")
    monkeypatch.setenv(marker, value)
    with pytest.raises(QaLeaseClosed):
        admission.CandidateQaAdmissionMiddleware(lambda request: JsonResponse({"ok": True}))


def test_fresh_gate_receives_resolved_tenant_and_ignores_forged_client_proof(
    monkeypatch, qa_env
):
    gate = Gate()
    monkeypatch.setattr(admission, "_gate", lambda: gate)
    request = _request()
    request.META["HTTP_X_QA_LEASE_ID"] = "forged"
    request.META["HTTP_X_QA_BINDING_SHA256"] = "replayed"
    response = admission.CandidateQaAdmissionMiddleware(
        lambda request: JsonResponse({"lease": request.candidate_qa_lease["lease_id"]})
    )(request)
    assert response.status_code == 200
    assert json.loads(response.content)["lease"] == "a" * 32
    assert len(gate.calls[0][0]) == 32
    assert gate.calls[0][1:] == (7, None)
    assert gate.active == 0
    second = _request()
    second.META["HTTP_X_REQUEST_ID"] = request.META["HTTP_X_REQUEST_ID"] = "same-client-id"
    assert admission.CandidateQaAdmissionMiddleware(
        lambda request: JsonResponse({"ok": True})
    )(second).status_code == 200
    assert gate.calls[1][0] != gate.calls[0][0]


def test_qa_worker_initializes_gate_before_first_request(monkeypatch, qa_env):
    gates = []
    monkeypatch.setattr(admission, "_gate", lambda: gates.append(Gate()) or gates[-1])
    middleware = admission.CandidateQaAdmissionMiddleware(lambda request: JsonResponse({"ok": True}))
    assert len(gates) == 1
    assert middleware(_request()).status_code == 200
    assert len(gates) == 1


def test_denied_mutation_does_not_run_view_but_recovery_read_stays_available(
    monkeypatch, qa_env
):
    gate = Gate(closed=True)
    monkeypatch.setattr(admission, "_gate", lambda: gate)
    called = []
    middleware = admission.CandidateQaAdmissionMiddleware(
        lambda request: called.append(request.method) or JsonResponse({"ok": True})
    )
    rejected = middleware(_request())
    read = middleware(_request("get", "/health", tenant_id=None))
    assert rejected.status_code == 503
    assert read.status_code == 200
    assert called == ["GET"]


def test_qa_tenantless_safe_reads_allow_only_exact_health_and_options(monkeypatch, qa_env):
    gate = Gate(closed=True)
    monkeypatch.setattr(admission, "_gate", lambda: gate)
    middleware = admission.CandidateQaAdmissionMiddleware(
        lambda request: JsonResponse({"ok": True})
    )
    for path in ("/health", "/health/", "/healthz", "/readyz/"):
        assert middleware(_request("get", path, tenant_id=None)).status_code == 200
    for path in ("/healthz-extra", "/admin/", "/api-auth/login/"):
        assert middleware(_request("get", path, tenant_id=None)).status_code == 503
    assert middleware(_request("options", "/api/v1/token/", tenant_id=None)).status_code == 200


def test_qa_denial_does_not_enqueue_generic_incident_audit(monkeypatch, qa_env, settings):
    settings.USER_INCIDENT_AUDIT_ASYNC = False
    settings.CORS_ALLOWED_ORIGINS = ["http://localhost:5173"]
    audits = []
    monkeypatch.setattr(common_middleware, "_persist_user_incident_audit", audits.append)
    monkeypatch.setattr(admission, "_gate", lambda: Gate(closed=True))
    inner = admission.CandidateQaAdmissionMiddleware(lambda request: JsonResponse({"ok": True}))
    middleware = common_middleware.CorsResponseFixMiddleware(
        common_middleware.UnhandledExceptionMiddleware(inner)
    )
    request = _request(path="/api/v1/exams/qa-denied/")
    request.META["HTTP_ORIGIN"] = "http://localhost:5173"
    response = middleware(request)
    assert response.status_code == 503
    assert json.loads(response.content)["code"] == "QA_WINDOW_CLOSED"
    assert response["Access-Control-Allow-Origin"] == "http://localhost:5173"
    assert request._candidate_qa_admission_denied is admission.DENIAL_MARKER
    assert audits == []

    # A caller-controlled header or a similarly named request value does not
    # suppress ordinary server-error auditing.
    other = _request(path="/api/v1/exams/qa-ordinary-error/")
    other.META["HTTP_X_CANDIDATE_QA_ADMISSION_DENIED"] = "true"
    other._candidate_qa_admission_denied = True
    ordinary = common_middleware.UnhandledExceptionMiddleware(
        lambda request: JsonResponse({"error": True}, status=503)
    )(other)
    assert ordinary.status_code == 503
    assert len(audits) == 1


@pytest.mark.parametrize("path", ["/api/v1/token/", "/api/v1/token/refresh/"])
def test_only_exact_auth_bootstrap_can_request_tenantless_admission(
    monkeypatch, qa_env, path
):
    gate = Gate()
    monkeypatch.setattr(admission, "_gate", lambda: gate)
    middleware = admission.CandidateQaAdmissionMiddleware(lambda request: JsonResponse({"ok": True}))
    assert middleware(_request(path=path, tenant_id=None)).status_code == 200
    assert gate.calls[0][1:] == (None, "auth")
    gate.calls.clear()
    assert middleware(_request(path=path + "extra", tenant_id=None)).status_code == 503
    assert gate.calls == []
    assert middleware(_request(path="/api-auth/login/", tenant_id=None)).status_code == 503
    assert gate.calls == []


def test_stream_remains_active_until_consumed_and_view_error_releases(monkeypatch, qa_env):
    from django.core.signals import request_finished

    monkeypatch.setattr(request_finished, "send", lambda **kwargs: [])
    gate = Gate()
    monkeypatch.setattr(admission, "_gate", lambda: gate)
    response = admission.CandidateQaAdmissionMiddleware(
        lambda request: StreamingHttpResponse(iter((b"result",)))
    )(_request())
    assert gate.active == 1
    assert b"".join(response.streaming_content) == b"result"
    assert gate.active == 0
    response.close()
    assert gate.active == 0

    abandoned = admission.CandidateQaAdmissionMiddleware(
        lambda request: StreamingHttpResponse(iter((b"not consumed",)))
    )(_request())
    assert gate.active == 1
    abandoned.close()
    assert gate.active == 0

    def fail(_request):
        raise ValueError("view failure")

    with pytest.raises(ValueError, match="view failure"):
        admission.CandidateQaAdmissionMiddleware(fail)(_request())
    assert gate.active == 0


def test_async_stream_releases_admission_after_consumption(monkeypatch, qa_env):
    gate = Gate()
    monkeypatch.setattr(admission, "_gate", lambda: gate)

    async def chunks():
        yield b"async-result"

    response = admission.CandidateQaAdmissionMiddleware(
        lambda request: StreamingHttpResponse(chunks())
    )(_request())
    assert gate.active == 1

    async def consume():
        return [chunk async for chunk in response.streaming_content]

    assert asyncio.run(consume()) == [b"async-result"]
    assert gate.active == 0


def test_committed_gate_denies_margin_drain_foreign_tenant_and_adapter_loss(
    monkeypatch, qa_env, tmp_path
):
    from apps.infrastructure.qa_lease import AdmissionGate, binding_sha256

    now = [1000]
    record = {
        "lease_id": "a" * 32,
        "owner_task": "01a0d04a-d64f-7473-9f58-61a8e983dcc0",
        "lock_owner": "candidate:123:1",
        "source_sha": "b" * 40,
        "images": {kind: "sha256:" + "c" * 64 for kind in ("api", "ai", "tools", "messaging")},
        "endpoint": "ssm://i-0123456789abcdef0:8001",
        "profile": "arn:aws:iam::809466760795:instance-profile/academy-api-qa",
        "scope": [509, 511],
        "baseline_sha256": "d" * 64,
        "tenant_ids": [7],
        "message_key_version": 1,
        "state": "active",
        "revision": 1,
        "started_at": 900,
        "renewed_at": 900,
        "expires_at": 1600,
    }
    monkeypatch.setenv("ACADEMY_QA_BINDING_SHA256", binding_sha256(record))
    reader = [lambda: (record, {"owner": "candidate:123:1", "expires_at": 2000})]
    gate = AdmissionGate(
        "api", context=dict(admission.os.environ), reader=lambda: reader[0](),
        clock=lambda: now[0], activity_dir=tmp_path, heartbeat=False,
    )
    monkeypatch.setattr(admission, "_gate", lambda: gate)
    calls = []
    middleware = admission.CandidateQaAdmissionMiddleware(
        lambda request: calls.append(request.method) or JsonResponse({"ok": True})
    )
    try:
        assert middleware(_request(tenant_id=7)).status_code == 200
        assert gate.snapshot()["inflight"] == []
        assert middleware(_request(tenant_id=8)).status_code == 503
        assert middleware(_request("get", tenant_id=8)).status_code == 503
        now[0] = 1570  # exactly expires_at - 30 seconds
        assert middleware(_request(tenant_id=7)).status_code == 503
        assert middleware(_request("get", tenant_id=7)).status_code == 200
        now[0] = 1600
        assert middleware(_request("get", tenant_id=7)).status_code == 503
        now[0] = 1200
        record["state"] = "draining"
        assert middleware(_request(tenant_id=7)).status_code == 503
        assert middleware(_request("get", tenant_id=7)).status_code == 200
        record["state"] = "hold"
        assert middleware(_request(tenant_id=7)).status_code == 503
        assert middleware(_request("get", tenant_id=7)).status_code == 503
        record["state"] = "active"
        reader[0] = lambda: (_ for _ in ()).throw(OSError("adapter unavailable"))
        assert middleware(_request(tenant_id=7)).status_code == 503
        assert middleware(_request("get", tenant_id=7)).status_code == 503
        assert calls == ["POST", "GET", "GET"]
    finally:
        gate.close()


def test_production_stack_never_registers_candidate_qa_middleware():
    from apps.api.config.settings.base import MIDDLEWARE

    assert "apps.api.middleware.candidate_qa_admission.CandidateQaAdmissionMiddleware" not in MIDDLEWARE
    production = (Path(__file__).parents[1] / "apps/api/config/settings/prod.py").read_text(encoding="utf-8")
    assert "candidate_qa_admission" not in production
