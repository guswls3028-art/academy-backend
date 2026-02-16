# PATH: apps/worker/ai_worker/ai/pipelines/excel_handler.py
# EXCEL_PARSING 작업 처리 — R2 다운로드 → 파싱·등록 → 로컬/R2 자원 정리

from __future__ import annotations

import logging
import os

from apps.shared.contracts.ai_result import AIResult
from apps.shared.contracts.ai_job import AIJob
from src.application.services.excel_parsing_service import ExcelParsingService
from src.infrastructure.storage import R2ObjectStorageAdapter

logger = logging.getLogger(__name__)

# 엑셀 버킷: 호출 시점 환경변수 참조 (하드코딩 금지)
def _excel_bucket(payload: dict) -> str:
    return (
        payload.get("bucket")
        or os.environ.get("EXCEL_BUCKET_NAME")
        or "academy-excel"
    )


def _record_progress(job_id: str, step: str, percent: int) -> None:
    """Redis 진행률 기록 (우하단 실시간 프로그래스바용)."""
    try:
        from src.infrastructure.cache.redis_progress_adapter import RedisProgressAdapter
        RedisProgressAdapter().record_progress(job_id, step, {"percent": percent})
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

    bucket = _excel_bucket(payload)
    storage = R2ObjectStorageAdapter()
    _record_progress(job.id, "downloading", 10)

    try:
        service = ExcelParsingService(storage)
        _record_progress(job.id, "parsing", 40)
        result = service.run(job.id, payload)
        _record_progress(job.id, "done", 100)
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
