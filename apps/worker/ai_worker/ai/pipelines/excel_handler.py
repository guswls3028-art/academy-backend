# PATH: apps/worker/ai_worker/ai/pipelines/excel_handler.py
# EXCEL_PARSING 작업 처리 — R2 다운로드 → 파싱·등록 → 로컬/R2 자원 정리

from __future__ import annotations

import logging
import os

from apps.shared.contracts.ai_result import AIResult
from apps.shared.contracts.ai_job import AIJob
from src.application.services.excel_parsing_service import ExcelParsingService
from apps.infrastructure.storage.r2_adapter import R2ObjectStorageAdapter

logger = logging.getLogger(__name__)

# 엑셀 버킷: 호출 시점 환경변수 참조 (하드코딩 금지)
def _excel_bucket(payload: dict) -> str:
    return (
        payload.get("bucket")
        or os.environ.get("EXCEL_BUCKET_NAME")
        or "academy-excel"
    )


# 엑셀 파싱 구간별 진행률 (n/4): 업로드 마법사처럼 단계별 0~100% 제공
EXCEL_PARSING_STEP_TOTAL = 4
EXCEL_PARSING_STEPS = [
    (1, "downloading", "다운로드"),
    (2, "parsing", "파싱"),
    (3, "enrolling", "등록"),
    (4, "done", "완료"),
]


def _record_progress(
    job_id: str,
    step: str,
    percent: int,
    step_index: int | None = None,
    step_percent: int | None = None,
    tenant_id: str | None = None,  # ✅ 추가: tenant_id 전달
) -> None:
    """Redis 진행률 기록 (우하단 실시간 프로그래스바용). 구간별 진행률 지원."""
    try:
        from src.infrastructure.cache.redis_progress_adapter import RedisProgressAdapter
        extra = {"percent": percent}
        if step_index is not None:
            extra.update({
                "step_index": step_index,
                "step_total": EXCEL_PARSING_STEP_TOTAL,
                "step_name": step,
                "step_name_display": dict(EXCEL_PARSING_STEPS).get(step, step),
                "step_percent": step_percent if step_percent is not None else 100,
            })
        # ✅ tenant_id 전달 (tenant namespace 키 사용)
        tenant_id_str = str(tenant_id) if tenant_id else None
        RedisProgressAdapter().record_progress(job_id, step, extra, tenant_id=tenant_id_str)
    except Exception as e:
        logger.debug("Redis progress record skip: %s", e)


def handle_excel_parsing_job(job: AIJob) -> AIResult:
    """
    EXCEL_PARSING 작업: R2에서 Get → ExcelParsingService(비즈니스 핵심) → 수강등록.
    어떤 상황(성공/예외)에서도 finally에서 R2 원본 객체 삭제 수행.
    """
    payload = job.payload or {}
    file_key = payload.get("file_key")
    if not file_key:
        return AIResult.failed(job.id, "payload.file_key required")

    # ✅ tenant_id 추출 (payload 우선, 없으면 job.tenant_id)
    tenant_id = str(payload.get("tenant_id") or job.tenant_id or "") if (payload.get("tenant_id") or job.tenant_id) else None

    bucket = _excel_bucket(payload)
    storage = R2ObjectStorageAdapter()
    _record_progress(job.id, "downloading", 10, step_index=1, step_percent=100, tenant_id=tenant_id)

    def _on_progress(step: str, percent: int) -> None:
        # ExcelParsingService에서 오는 step: "parsing", "creating", "enrolling" 등
        if step == "parsing":
            _record_progress(job.id, "parsing", 40, step_index=2, step_percent=100, tenant_id=tenant_id)
        elif step == "creating":
            # 학생만 생성: percent가 40~95 사이로 오므로 step_percent 계산
            step_pct = int(100 * (percent - 40) / 55) if percent > 40 else 0
            step_pct = min(100, max(0, step_pct))
            _record_progress(job.id, "enrolling", percent, step_index=3, step_percent=step_pct, tenant_id=tenant_id)
        elif step == "enrolling":
            # 수강 등록: percent가 50~95 사이로 오므로 step_percent 계산
            step_pct = int(100 * (percent - 50) / 45) if percent > 50 else 0
            step_pct = min(100, max(0, step_pct))
            _record_progress(job.id, "enrolling", percent, step_index=3, step_percent=step_pct, tenant_id=tenant_id)
        else:
            _record_progress(job.id, step, percent, tenant_id=tenant_id)

    try:
        service = ExcelParsingService(storage)
        _record_progress(job.id, "parsing", 25, step_index=2, step_percent=0, tenant_id=tenant_id)
        result = service.run(job.id, payload, on_progress=_on_progress)
        _record_progress(job.id, "done", 100, step_index=4, step_percent=100, tenant_id=tenant_id)
        result["processed_by"] = "worker"
        logger.info(
            "EXCEL_PARSING processed_by=worker job_id=%s enrolled=%s",
            job.id,
            result.get("enrolled_count"),
        )
        return AIResult.done(job.id, result)
    except Exception as e:
        logger.exception(
            "EXCEL_PARSING failed job_id=%s tenant_id=%s: %s",
            job.id,
            payload.get("tenant_id"),
            e,
        )
        return AIResult.failed(job.id, str(e)[:2000])
    finally:
        # 더블 체크: 성공/실패/예외와 관계없이 R2 원본 삭제 (로컬 tmp는 ExcelParsingService.run finally에서 정리)
        try:
            storage.delete_object(bucket, file_key)
            logger.debug("EXCEL_PARSING R2 cleanup done bucket=%s key=%s", bucket, file_key)
        except Exception as e:
            logger.warning("R2 delete_object after EXCEL_PARSING bucket=%s key=%s: %s", bucket, file_key, e)
