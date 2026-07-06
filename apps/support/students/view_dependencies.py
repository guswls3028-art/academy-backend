"""Cross-domain dependencies for student views."""

from __future__ import annotations

from typing import Any


def send_welcome_messages(**kwargs: Any) -> Any:
    from apps.domains.messaging.services import send_welcome_messages as _send

    return _send(**kwargs)


def get_tenant_site_url(tenant: Any) -> str:
    from apps.domains.messaging.services import get_tenant_site_url as _get_url

    return _get_url(tenant)


def send_event_notification(**kwargs: Any) -> Any:
    from apps.domains.messaging.services import send_event_notification as _send

    return _send(**kwargs)


def dispatch_job(**kwargs: Any) -> dict:
    from apps.domains.ai.gateway import dispatch_job as _dispatch

    return _dispatch(**kwargs)


def get_excel_parsing_job_status_response(*, job_id: str, tenant_id: str) -> dict | None:
    from academy.adapters.db.django.repositories_ai import DjangoAIJobRepository
    from apps.domains.ai.services.job_status_response import build_job_status_response

    repo = DjangoAIJobRepository()
    job = repo.get_job_model_for_status(job_id, tenant_id, job_type="excel_parsing")
    if not job:
        return None
    return build_job_status_response(job)
