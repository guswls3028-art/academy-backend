# PATH: apps/worker/ai_worker/ai/pipelines/matchup_manual_index.py
# 수동 크롭으로 만든 단일 MatchupProblem 이미지 → OCR + 임베딩.
"""
payload: {problem_id, tenant_id, image_key}

수동 크롭은 동기적으로 problem 레코드 + R2 이미지를 만들지만 임베딩이 비어
있어 매치업 검색에 노출되지 않는다. 이 잡이 비동기로 OCR + 임베딩을 채워
검색 풀에 합류시킨다.

흐름:
  1. R2에서 problem 이미지 다운로드 (presigned URL)
  2. Google Vision OCR 호출 → 텍스트 추출
  3. text_for_embedding 정제 → 임베딩 생성
  4. 결과 dict 반환 → callback이 DB 업데이트
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict

from apps.shared.contracts.ai_job import AIJob
from apps.shared.contracts.ai_result import AIResult

logger = logging.getLogger(__name__)


def run_matchup_manual_index(
    *,
    job: AIJob,
    payload: Dict[str, Any],
    tenant_id: str | None,
    record_progress: Callable,
) -> AIResult:
    job_id = str(job.id)
    problem_id = payload.get("problem_id")
    image_key = payload.get("image_key") or ""

    if not problem_id:
        return AIResult.failed(job_id, "problem_id missing")
    if not image_key:
        return AIResult.failed(job_id, "image_key missing")

    record_progress(
        job_id, "downloading", 10,
        step_index=1, step_total=3,
        step_name_display="이미지 다운로드",
        step_percent=0, tenant_id=tenant_id,
    )

    # 1) 이미지 다운로드
    try:
        from apps.infrastructure.storage.r2 import generate_presigned_get_url_storage
    except ImportError:
        return AIResult.failed(job_id, "R2 storage not available")

    from apps.worker.ai_worker.storage.downloader import (
        cleanup_tmp_for_path,
        download_to_tmp,
    )

    url = generate_presigned_get_url_storage(key=image_key, expires_in=600)
    if not url:
        return AIResult.failed(job_id, "presign failed")

    local_path = None
    try:
        local_path = download_to_tmp(download_url=url, job_id=job_id)
    except Exception as e:
        return AIResult.failed(job_id, f"download failed: {e}")

    record_progress(
        job_id, "ocr", 40,
        step_index=2, step_total=3,
        step_name_display="텍스트 추출",
        step_percent=0, tenant_id=tenant_id,
    )

    # 2) OCR
    text = ""
    try:
        from apps.worker.ai_worker.ai.ocr.google import google_ocr
        result = google_ocr(local_path)
        text = (result.text or "").strip() if hasattr(result, "text") else ""
    except Exception:
        logger.warning("manual_index: google_ocr failed for problem=%s", problem_id, exc_info=True)
        text = ""
    finally:
        cleanup_tmp_for_path(local_path)

    record_progress(
        job_id, "embedding", 70,
        step_index=3, step_total=3,
        step_name_display="AI 분석",
        step_percent=0, tenant_id=tenant_id,
    )

    # 3) 임베딩 — 정제 텍스트 사용
    embedding = None
    cleaned = ""
    if text:
        try:
            from apps.worker.ai_worker.ai.pipelines.matchup_pipeline import (
                normalize_text_for_embedding,
                detect_format,
            )
            cleaned = normalize_text_for_embedding(text)
        except Exception:
            cleaned = text

        if cleaned.strip():
            try:
                from apps.worker.ai_worker.ai.embedding.service import get_embeddings
                batch = get_embeddings([cleaned])
                if batch.vectors:
                    embedding = batch.vectors[0]
            except Exception:
                logger.warning("manual_index: embedding failed for problem=%s", problem_id, exc_info=True)

    fmt = "choice"
    try:
        from apps.worker.ai_worker.ai.pipelines.matchup_pipeline import detect_format
        fmt = detect_format(text)
    except Exception:
        pass

    record_progress(
        job_id, "done", 100,
        step_index=3, step_total=3,
        step_name_display="완료",
        step_percent=100, tenant_id=tenant_id,
    )

    return AIResult.done(job_id, {
        "problem_id": int(problem_id),
        "text": text,
        "embedding": embedding,
        "format": fmt,
    })
