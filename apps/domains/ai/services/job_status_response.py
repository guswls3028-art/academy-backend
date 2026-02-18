# apps/domains/ai/services/job_status_response.py
# Job 상태 조회 응답 생성 (GET /jobs/<id>/ 및 enrollments/excel_job_status 공통)

from __future__ import annotations


def build_job_status_response(job, result_payload=None) -> dict:
    """
    AIJobModel 인스턴스로부터 API 응답 dict 생성.
    - result: result_payload (caller 전달 또는 repository에서 조회)
    - progress: Redis 진행률 (있으면)
    """
    if result_payload is None:
        from academy.adapters.db.django.repositories_ai import DjangoAIJobRepository
        result_payload = DjangoAIJobRepository().get_result_payload_for_job(job)
    progress = None
    try:
        from src.infrastructure.cache.redis_progress_adapter import RedisProgressAdapter
        # ✅ tenant_id 전달 필수 (tenant namespace 키 사용)
        tenant_id = str(job.tenant_id) if job.tenant_id else None
        progress = RedisProgressAdapter().get_progress(job.job_id, tenant_id=tenant_id)
    except Exception:
        pass

    return {
        "job_id": job.job_id,
        "job_type": job.job_type,
        "status": job.status,
        "result": result_payload,
        "error_message": job.error_message or None,
        "progress": progress,
    }
