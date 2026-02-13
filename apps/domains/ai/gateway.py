from __future__ import annotations

from typing import Any, Dict, Optional

import logging

from django.db import IntegrityError

from apps.shared.contracts.ai_job import AIJob
from apps.domains.ai.types import ensure_payload_dict, AIJobType
from apps.domains.ai.safe import safe_dispatch
from apps.domains.ai.queueing.publisher import publish_job
from apps.domains.ai.models import AIJobModel
from apps.domains.ai.services.tier_resolver import resolve_tier, validate_tier_for_job_type
from apps.domains.ai.services.pre_validation import validate_input_for_basic

logger = logging.getLogger(__name__)


def dispatch_job(
    *,
    job_type: AIJobType,
    payload: Dict[str, Any],
    tenant_id: Optional[str] = None,
    source_domain: Optional[str] = None,
    source_id: Optional[str] = None,
    tier: Optional[str] = None,  # "lite" | "basic" | "premium"
    idempotency_key: Optional[str] = None,
    force_rerun: bool = False,
    rerun_reason: Optional[str] = None,
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

    # Pre-Validation (Lite/Basic): 거부 정책 해당 시 job 생성 없이 반환
    if tier in ("lite", "basic"):
        ok, error_message, rejection_code = validate_input_for_basic(
            tier=tier,
            job_type=job_type,
            payload=payload,
        )
        if not ok:
            logger.info(
                "ai_pre_validation_rejected tier=%s job_type=%s rejection_code=%s",
                tier,
                job_type,
                rejection_code or "unknown",
                extra={"rejection_code": rejection_code, "job_type": job_type, "tenant_id": tenant_id},
            )
            return {
                "ok": False,
                "job_id": None,
                "type": job_type,
                "error": error_message or "Validation failed",
                "rejection_code": rejection_code,
            }

    job = AIJob.new(
        type=job_type,
        payload=payload,
        tenant_id=tenant_id,
        source_domain=source_domain,
        source_id=source_id,
    )

    def _do():
        # Idempotency: 동시 요청 시 500 방지 (IntegrityError → 기존 Job 반환)
        effective_key = idempotency_key
        if effective_key and force_rerun:
            effective_key = f"{effective_key}:rerun:{job.id}"

        if effective_key:
            try:
                job_model = AIJobModel.objects.create(
                    job_id=job.id,
                    job_type=job.type,
                    payload=job.payload,
                    status="PENDING",
                    tier=tier,
                    tenant_id=tenant_id,
                    source_domain=source_domain,
                    source_id=source_id,
                    idempotency_key=effective_key,
                    force_rerun=force_rerun,
                    rerun_reason=(rerun_reason or ""),
                )
            except IntegrityError:
                job_model = AIJobModel.objects.get(idempotency_key=effective_key)
                return {"ok": True, "job_id": str(job_model.job_id), "type": job_model.job_type}
        else:
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
