"""Admit candidate QA mutations only under the committed runtime lease.

The shared infrastructure gate owns trusted lease and runtime verification. This
middleware never treats request headers, body, or query parameters as lease proof.
"""

from __future__ import annotations

import logging
import os
from threading import Lock
from uuid import uuid4

from django.http import JsonResponse
from django.urls import Resolver404, resolve


logger = logging.getLogger(__name__)
DENIAL_MARKER = object()
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_TENANTLESS_HEALTH_PATHS = frozenset(
    {"/health", "/health/", "/healthz", "/healthz/", "/readyz", "/readyz/"}
)
_AUTH_BOOTSTRAP_ROUTES = {
    "/api/v1/token/": "token_obtain_pair",
    "/api/v1/token/refresh/": "token_refresh",
}
_QA_ENV_KEYS = (
    "ACADEMY_QA_MODE",
    "ACADEMY_QA_LEASE_ID",
    "ACADEMY_QA_BINDING_SHA256",
)


def _qa_requested() -> bool:
    # A partial QA configuration must be admitted by the gate and fail closed.
    required = os.environ.get("CANDIDATE_LEASE_REQUIRED", "").strip().lower()
    return required not in {"", "false", "0"} or any(
        os.environ.get(key, "").strip() for key in _QA_ENV_KEYS
    )


def _qa_required() -> bool:
    return os.environ.get("CANDIDATE_LEASE_REQUIRED", "").strip().lower() in {"true", "1"}


def _gate():
    # The shared gate belongs to the release owner and is deliberately loaded
    # at QA worker startup. A missing adapter must not prevent health reads.
    from apps.infrastructure.qa_lease import get_admission_gate

    return get_admission_gate("api")


def _is_auth_bootstrap(request) -> bool:
    if request.method.upper() != "POST":
        return False
    expected = _AUTH_BOOTSTRAP_ROUTES.get(request.path_info)
    if expected is None:
        return False
    try:
        return resolve(request.path_info).view_name == expected
    except Resolver404:
        return False


def _closed_response(request) -> JsonResponse:
    # A denial happens after new work is closed. Do not let the generic 5xx
    # incident sampler create a fresh audit row for this expected response.
    request._candidate_qa_admission_denied = DENIAL_MARKER
    logger.warning("Candidate QA API admission denied code=QA_WINDOW_CLOSED method=%s", request.method)
    response = JsonResponse(
        {
            "code": "QA_WINDOW_CLOSED",
            "detail": "후보 QA 작업을 시작할 수 없습니다. QA 기간과 연결 상태를 확인한 뒤 다시 시도해 주세요.",
        },
        status=503,
    )
    response["Cache-Control"] = "no-store"
    return response


class CandidateQaAdmissionMiddleware:
    """Track each QA mutation through completion; leave recovery reads open."""

    def __init__(self, get_response):
        self.get_response = get_response
        self.gate = None
        if _qa_requested():
            # Malformed/partial QA identity fails process startup. Every valid
            # QA worker publishes its heartbeat before handling its first job.
            self.gate = _gate()

    def __call__(self, request):
        if not _qa_requested():
            return self.get_response(request)
        method = request.method.upper()
        if method in _SAFE_METHODS:
            if method == "OPTIONS" or request.path_info in _TENANTLESS_HEALTH_PATHS:
                return self.get_response(request)
            tenant = getattr(request, "tenant", None)
            if tenant is None or self.gate is None:
                return _closed_response(request)
            try:
                lease = self.gate.inspect_lease()
                self.gate.assert_tenant(lease, tenant.pk)
                if lease["state"] not in {"active", "draining"} or int(self.gate.clock()) >= int(lease["expires_at"]):
                    return _closed_response(request)
            except Exception as exc:
                logger.warning("Candidate QA API read gate rejected reason_type=%s", type(exc).__name__)
                return _closed_response(request)
            return self.get_response(request)

        # Registration is development-only, but a misregistered middleware must
        # still refuse a configured QA mutation outside that runtime.
        if os.environ.get("ACADEMY_RUNTIME_ENV", "").strip().lower() != "development":
            return _closed_response(request)

        try:
            gate = self.gate
            if gate is None:
                return _closed_response(request)
            tenant = getattr(request, "tenant", None)
            tenant_id = getattr(tenant, "pk", None)
            purpose = "auth" if tenant_id is None and _is_auth_bootstrap(request) else None
            if tenant_id is None and purpose is None:
                return _closed_response(request)
            admission = gate.begin(uuid4().hex, tenant_id=tenant_id, purpose=purpose)
            lease = admission.__enter__()
        except Exception as exc:
            logger.warning("Candidate QA API gate rejected reason_type=%s", type(exc).__name__)
            return _closed_response(request)

        try:
            # The gate provides the committed lease; never copy a client value.
            request.candidate_qa_lease = lease
            response = self.get_response(request)
        except BaseException as exc:
            admission.__exit__(type(exc), exc, exc.__traceback__)
            raise

        if not getattr(response, "streaming", False):
            admission.__exit__(None, None, None)
            return response

        release_lock = Lock()
        released = False

        def release():
            nonlocal released
            with release_lock:
                if released:
                    return
                released = True
            admission.__exit__(None, None, None)

        try:
            original = response.streaming_content
            if getattr(response, "is_async", False):
                async def tracked_async_stream():
                    try:
                        async for chunk in original:
                            yield chunk
                    finally:
                        release()

                response.streaming_content = tracked_async_stream()
            else:
                def tracked_stream():
                    try:
                        yield from original
                    finally:
                        release()

                response.streaming_content = tracked_stream()
            # Django closes response resources even if a client disconnects
            # before the generator's first iteration (when finally cannot run).
            response._resource_closers.append(release)
        except BaseException:
            release()
            raise
        return response
