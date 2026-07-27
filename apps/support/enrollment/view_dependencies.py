"""Cross-domain dependencies for enrollment views."""

from __future__ import annotations

from typing import Any


def dispatch_job(**kwargs: Any) -> dict:
    from apps.domains.ai.gateway import dispatch_job as _dispatch

    return _dispatch(**kwargs)


def protect_excel_initial_password(initial_password: str) -> dict[str, str]:
    from apps.domains.ai.services.excel_job_secrets import (
        protect_excel_initial_password as _protect,
    )

    return _protect(initial_password)


def get_excel_parsing_job_status_response(*, job_id: str, tenant_id: str) -> dict | None:
    from academy.adapters.db.django.repositories_ai import DjangoAIJobRepository
    from apps.domains.ai.services.job_status_response import build_job_status_response

    repo = DjangoAIJobRepository()
    job = repo.get_job_model_for_status(job_id, tenant_id, job_type="excel_parsing")
    if not job:
        return None
    return build_job_status_response(job, include_excel_credentials=True)
