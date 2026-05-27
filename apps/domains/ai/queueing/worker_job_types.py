"""AI job routing contracts for worker families."""

TOOL_WORKER_JOB_TYPES = frozenset({"ppt_generation"})


def is_tool_worker_job_type(job_type: str | None) -> bool:
    return (job_type or "").strip().lower() in TOOL_WORKER_JOB_TYPES
