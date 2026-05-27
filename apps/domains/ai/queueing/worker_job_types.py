"""AI job routing contracts for worker families."""

from apps.domains.ai.job_types import TOOL_WORKER_JOB_TYPES


def is_tool_worker_job_type(job_type: str | None) -> bool:
    return (job_type or "").strip().lower() in TOOL_WORKER_JOB_TYPES
