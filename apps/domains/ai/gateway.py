from __future__ import annotations

from typing import Any, Dict, Optional

import logging

from apps.shared.contracts.ai_job import AIJob
from apps.domains.ai.types import ensure_payload_dict, AIJobType
from apps.domains.ai.safe import safe_dispatch
from apps.domains.ai.queueing.publisher import publish_job
from apps.domains.ai.models import AIJobModel
from apps.domains.ai.services.tier_resolver import resolve_tier, validate_tier_for_job_type

logger = logging.getLogger(__name__)


def dispatch_job(
    *,
    job_type: AIJobType,
    payload: Dict[str, Any],
    tenant_id: Optional[str] = None,
    source_domain: Optional[str] = None,
    source_id: Optional[str] = None,
    tier: Optional[str] = None,  # "lite" | "basic" | "premium"
) -> Dict[str, Any]:
    """
    AI 작업 발행
    
    Args:
        job_type: 작업 타입
        payload: 작업 페이로드
        tenant_id: Tenant ID
        source_domain: 소스 도메인
        source_id: 소스 ID
        tier: Tier ("lite" | "basic" | "premium"), 기본값: "basic"
    """
    payload = ensure_payload_dict(payload)
    
    # Tier 결정 (명시적 tier 또는 자동 결정)
    if not tier:
        tier = resolve_tier(
            tenant_id=tenant_id,
            job_type=job_type,
            payload=payload,
        )
    tier = tier.lower()
    if tier not in ("lite", "basic", "premium"):
        tier = "basic"
    
    # Tier와 작업 타입 호환성 검증
    if not validate_tier_for_job_type(tier, job_type):
        logger.warning(
            "Tier %s is not compatible with job_type %s, using basic",
            tier,
            job_type,
        )
        tier = "basic"

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
            tier=tier,
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
