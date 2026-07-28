from __future__ import annotations

from apps.shared.contracts.ai_result import AIResult
from apps.shared.contracts.ai_job import AIJob
from apps.domains.tools.problem_studio.services import build_problem_studio_package_from_worker_payload


def handle_problem_studio_package_job(job: AIJob) -> AIResult:
    payload = job.payload or {}
    payload_tenant_id = payload.get("tenant_id")
    if not job.tenant_id or str(payload_tenant_id or "") != str(job.tenant_id):
        return AIResult.failed(job.id, "tenant_id mismatch")
    if not payload.get("request_user_id"):
        return AIResult.failed(job.id, "request_user_id missing")
    result = build_problem_studio_package_from_worker_payload(payload)
    return AIResult.done(job.id, result)
