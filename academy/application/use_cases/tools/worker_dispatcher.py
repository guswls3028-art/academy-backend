"""Tools worker dispatcher for deterministic document jobs."""

from __future__ import annotations

from apps.shared.contracts.ai_job import AIJob
from apps.shared.contracts.ai_result import AIResult


def handle_tools_job(job: AIJob) -> AIResult:
    job_type = (job.type or "").strip().lower()

    if job_type == "ppt_generation":
        from academy.application.use_cases.ai.pipelines.ppt_handler import handle_ppt_generation_job

        return handle_ppt_generation_job(job)

    if job_type == "excel_parsing":
        from academy.application.use_cases.ai.pipelines.excel_handler import handle_excel_parsing_job

        return handle_excel_parsing_job(job)

    if job_type == "attendance_excel_export":
        from academy.application.use_cases.ai.pipelines.excel_export_handler import handle_attendance_excel_export

        return handle_attendance_excel_export(job)

    if job_type == "staff_excel_export":
        from academy.application.use_cases.ai.pipelines.excel_export_handler import handle_staff_excel_export

        return handle_staff_excel_export(job)

    return AIResult.failed(job.id, f"Unsupported tools job type: {job.type}")
