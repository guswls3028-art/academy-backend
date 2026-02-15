"""
AI Job Repository — Django ORM 구현 (메서드 내부에서만 apps.domains.ai import)
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from academy.domain.ai.entities import AIJob, AIJobStatus  # noqa: F401


def _model_to_entity(m) -> Optional[AIJob]:
    if m is None:
        return None
    return AIJob(
        job_id=m.job_id,
        job_type=m.job_type,
        status=AIJobStatus(m.status) if m.status else AIJobStatus.PENDING,
        payload=m.payload or {},
        tenant_id=m.tenant_id,
        source_domain=m.source_domain,
        source_id=m.source_id,
        tier=m.tier or "basic",
        attempt_count=int(m.attempt_count or 0),
        max_attempts=int(m.max_attempts or 5),
        locked_by=m.locked_by,
        locked_at=m.locked_at,
        lease_expires_at=m.lease_expires_at,
        idempotency_key=m.idempotency_key,
        error_message=(m.error_message or "")[:2000],
        updated_at=getattr(m, "updated_at", None),
    )


class DjangoAIJobRepository:
    """AIJobRepository 구현. ORM 접근은 모두 메서드 내부에서 lazy import."""

    def get_by_job_id(self, job_id: str) -> Optional[AIJob]:
        from apps.domains.ai.models import AIJobModel
        m = AIJobModel.objects.filter(job_id=job_id).first()
        return _model_to_entity(m)

    def get_for_update(self, job_id: str) -> Optional[AIJob]:
        """호출자가 이미 UoW 트랜잭션 내에 있어야 함 (select_for_update 락 유지)."""
        from apps.domains.ai.models import AIJobModel
        m = AIJobModel.objects.select_for_update().filter(job_id=job_id).first()
        return _model_to_entity(m)

    def save(self, job: AIJob) -> None:
        from django.utils import timezone
        from apps.domains.ai.models import AIJobModel
        now = timezone.now()
        AIJobModel.objects.update_or_create(
            job_id=job.job_id,
            defaults={
                "job_type": job.job_type,
                "status": job.status.value,
                "payload": job.payload,
                "tenant_id": job.tenant_id,
                "source_domain": job.source_domain,
                "source_id": job.source_id,
                "tier": job.tier,
                "attempt_count": job.attempt_count,
                "max_attempts": job.max_attempts,
                "locked_by": job.locked_by,
                "locked_at": job.locked_at,
                "lease_expires_at": job.lease_expires_at,
                "idempotency_key": job.idempotency_key,
                "error_message": job.error_message,
                "updated_at": now,
            },
        )

    def mark_running(
        self,
        job_id: str,
        worker_id: str,
        lease_expires_at: datetime,
        now: datetime,
    ) -> bool:
        from apps.domains.ai.models import AIJobModel
        job = AIJobModel.objects.select_for_update().filter(job_id=job_id).first()
        if not job:
            return False
        if job.status == "RUNNING":
            return True
        if job.status not in ("PENDING", "RETRYING"):
            return False
        job.status = "RUNNING"
        job.locked_by = str(worker_id)
        job.locked_at = now
        job.lease_expires_at = lease_expires_at
        job.last_heartbeat_at = now
        job.save(update_fields=["status", "locked_by", "locked_at", "lease_expires_at", "last_heartbeat_at", "updated_at"])
        return True

    def mark_done(self, job_id: str, now: datetime, result_payload: Optional[dict] = None) -> bool:
        from apps.domains.ai.models import AIJobModel, AIResultModel
        job = AIJobModel.objects.select_for_update().filter(job_id=job_id).first()
        if not job:
            return False
        if job.status == "DONE":
            if result_payload is not None:
                res, _ = AIResultModel.objects.get_or_create(job=job, defaults={"payload": result_payload})
                if res.payload != result_payload:
                    res.payload = result_payload
                    res.save(update_fields=["payload"])
            return True
        job.status = "DONE"
        job.locked_by = None
        job.locked_at = None
        job.lease_expires_at = None
        job.save(update_fields=["status", "locked_by", "locked_at", "lease_expires_at", "updated_at"])
        if result_payload is not None:
            res, _ = AIResultModel.objects.get_or_create(job=job, defaults={"payload": result_payload})
            if res.payload != result_payload:
                res.payload = result_payload
                res.save(update_fields=["payload"])
        return True

    def mark_failed(
        self,
        job_id: str,
        error_message: str,
        tier: str,
        now: datetime,
    ) -> bool:
        from apps.domains.ai.models import AIJobModel
        from apps.domains.ai.services.status_resolver import status_for_exception
        job = AIJobModel.objects.select_for_update().filter(job_id=job_id).first()
        if not job:
            return False
        final_str, _ = status_for_exception(tier or job.tier or "basic")
        if job.status == final_str:
            return True
        err = (error_message or "")[:2000]
        job.status = final_str
        job.error_message = err
        job.last_error = err
        job.locked_by = None
        job.locked_at = None
        job.lease_expires_at = None
        job.save(update_fields=["status", "error_message", "last_error", "locked_by", "locked_at", "lease_expires_at", "updated_at"])
        return True

    def get_job_model_for_status(self, job_id: str, tenant_id: str, job_type: Optional[str] = None):
        """API 상태 조회용: tenant 일치 시 모델 인스턴스 반환 (adapters 내부에서만 .objects. 사용). job_type 지정 시 해당 타입만."""
        from apps.domains.ai.models import AIJobModel
        qs = AIJobModel.objects.filter(job_id=job_id, tenant_id=tenant_id)
        if job_type is not None:
            qs = qs.filter(job_type=job_type)
        return qs.first()

    def get_result_payload_for_job(self, job_model) -> Optional[dict]:
        """job(AIJobModel)에 대한 결과 payload 반환."""
        from apps.domains.ai.models import AIResultModel
        row = AIResultModel.objects.filter(job=job_model).first()
        return row.payload if row else None


# ---------------------------------------------------------------------------
# 모듈 레벨 함수 (gateway, callbacks, publisher, internal_ai_job_view 등에서 사용)
# ---------------------------------------------------------------------------


def get_job_model_for_status(job_id: str, tenant_id: str, job_type: Optional[str] = None):
    """API 상태 조회용: tenant 일치 시 모델 인스턴스 반환. job_type 지정 시 해당 타입만."""
    from apps.domains.ai.models import AIJobModel
    qs = AIJobModel.objects.filter(job_id=job_id, tenant_id=tenant_id)
    if job_type is not None:
        qs = qs.filter(job_type=job_type)
    return qs.first()


def get_job_model_by_job_id(job_id: str):
    """job_id로 AIJobModel 조회 (tenant 무관, callbacks/publisher/internal용)."""
    from apps.domains.ai.models import AIJobModel
    return AIJobModel.objects.filter(job_id=str(job_id)).first()


def job_create(job_id: str, job_type: str, payload: dict, status: str = "PENDING", tier: str = "basic",
               tenant_id=None, source_domain=None, source_id=None, idempotency_key=None,
               force_rerun=False, rerun_reason=None, **extra):
    """AIJobModel 1건 생성. 반환: job_model."""
    from apps.domains.ai.models import AIJobModel
    return AIJobModel.objects.create(
        job_id=job_id,
        job_type=job_type,
        payload=payload,
        status=status,
        tier=tier,
        tenant_id=tenant_id,
        source_domain=source_domain,
        source_id=source_id,
        idempotency_key=idempotency_key,
        force_rerun=force_rerun,
        rerun_reason=rerun_reason or "",
        **extra,
    )


def job_get_by_idempotency_key(key: str):
    """idempotency_key로 AIJobModel 1건 조회."""
    from apps.domains.ai.models import AIJobModel
    return AIJobModel.objects.get(idempotency_key=key)


def job_save_failed(job_model, error_message: str, last_error: str) -> None:
    """job_model 상태를 FAILED로 저장 (publish 실패 시)."""
    job_model.status = "FAILED"
    job_model.error_message = error_message
    job_model.last_error = last_error
    job_model.save(update_fields=["status", "error_message", "last_error", "updated_at"])


def result_exists_for_job(job_model) -> bool:
    """해당 job에 대한 AIResultModel 존재 여부."""
    from apps.domains.ai.models import AIResultModel
    return AIResultModel.objects.filter(job=job_model).exists()


def result_create(job_model, payload) -> None:
    """AIResultModel 1건 생성."""
    from apps.domains.ai.models import AIResultModel
    AIResultModel.objects.create(job=job_model, payload=payload)


def get_airuntime_config(key: str):
    """AIRuntimeConfigModel key로 1건 조회."""
    from apps.domains.ai.models import AIRuntimeConfigModel
    return AIRuntimeConfigModel.objects.filter(key=key).first()


def get_tenant_config(tenant_id: str):
    """TenantConfigModel tenant_id로 1건 조회."""
    from apps.domains.ai.models import TenantConfigModel
    return TenantConfigModel.objects.filter(tenant_id=tenant_id).first()
