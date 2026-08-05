"""Tools worker dispatcher for deterministic document jobs."""

from __future__ import annotations

from apps.shared.contracts.ai_job import AIJob
from apps.shared.contracts.ai_result import AIResult


def handle_tools_job(job: AIJob) -> AIResult:
    job_type = (job.type or "").strip().lower()

    if job_type == "ppt_generation":
        from academy.application.use_cases.ai.pipelines.ppt_handler import handle_ppt_generation_job

        return handle_ppt_generation_job(job)

    if job_type == "problem_studio_transfer":
        from academy.application.use_cases.ai.pipelines.problem_studio_transfer_handler import (
            handle_problem_studio_transfer_job,
        )

        return handle_problem_studio_transfer_job(job)

    if job_type == "excel_parsing":
        from academy.application.use_cases.ai.pipelines.excel_handler import handle_excel_parsing_job

        return handle_excel_parsing_job(job)

    if job_type == "attendance_excel_export":
        from academy.application.use_cases.ai.pipelines.excel_export_handler import handle_attendance_excel_export

        return handle_attendance_excel_export(job)

    if job_type == "staff_excel_export":
        from academy.application.use_cases.ai.pipelines.excel_export_handler import handle_staff_excel_export

        return handle_staff_excel_export(job)

    if job_type == "wrong_note_pdf_generation":
        from apps.domains.results.services.wrong_note_pdf_worker import (
            handle_wrong_note_pdf_generation_job,
        )

        return handle_wrong_note_pdf_generation_job(job)

    if job_type == "problem_review_export":
        from apps.domains.tools.problem_review.worker import (
            handle_problem_review_export_job,
        )

        return handle_problem_review_export_job(job)

    return AIResult.failed(job.id, f"Unsupported tools job type: {job.type}")
