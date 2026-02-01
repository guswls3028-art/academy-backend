from __future__ import annotations

from typing import Any, Dict, Optional

from apps.shared.contracts.ai_job import AIJob
from apps.domains.ai.types import ensure_payload_dict, AIJobType
from apps.domains.ai.safe import safe_dispatch
from apps.domains.ai.queueing.publisher import publish_job
from apps.domains.ai.models import AIJobModel


def dispatch_job(
    *,
    job_type: AIJobType,
    payload: Dict[str, Any],
    tenant_id: Optional[str] = None,
    source_domain: Optional[str] = None,
    source_id: Optional[str] = None,
) -> Dict[str, Any]:
    payload = ensure_payload_dict(payload)

    job = AIJob.new(
        type=job_type,
        payload=payload,
        tenant_id=tenant_id,
        source_domain=source_domain,
        source_id=source_id,
    )

    def _do():
        # ✅ callbacks가 AIJobModel을 조회하므로, 발행 시점에 반드시 저장
        job_model = AIJobModel.objects.create(
            job_id=job.id,
            job_type=job.type,
            payload=job.payload,
            status="PENDING",
            tenant_id=tenant_id,
            source_domain=source_domain,
            source_id=source_id,
        )

        try:
            publish_job(job)
        except Exception as e:
            job_model.status = "FAILED"
            job_model.error_message = str(e)
            job_model.last_error = str(e)
            job_model.save(update_fields=["status", "error_message", "last_error", "updated_at"])
            raise

        return {"ok": True, "job_id": job.id, "type": job.type}

    return safe_dispatch(_do, fallback={"ok": False, "job_id": job.id, "type": job.type})
