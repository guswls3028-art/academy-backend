from __future__ import annotations

from apps.shared.contracts.ai_result import AIResult
from apps.shared.contracts.ai_job import AIJob
from apps.domains.tools.problem_studio.services import build_problem_studio_package_from_worker_payload


def handle_problem_studio_package_job(job: AIJob) -> AIResult:
    result = build_problem_studio_package_from_worker_payload(job.payload or {})
    return AIResult.done(job.id, result)
