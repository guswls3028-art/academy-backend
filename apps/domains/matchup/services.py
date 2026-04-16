# PATH: apps/domains/matchup/services.py
# 매치업 비즈니스 로직 — 유사도 검색, R2 정리, 재시도

from __future__ import annotations

import logging
from typing import List, Tuple

from apps.shared.utils.vector import cosine_similarity
from .models import MatchupDocument, MatchupProblem

logger = logging.getLogger(__name__)

try:
    from apps.infrastructure.storage.r2 import delete_object_r2_storage
except ImportError:
    delete_object_r2_storage = None  # type: ignore


def find_similar_problems(
    problem_id: int, tenant_id: int, top_k: int = 10
) -> List[Tuple["MatchupProblem", float]]:
    """
    주어진 문제와 유사한 문제를 코사인 유사도 기준으로 찾는다.
    Returns: [(problem, similarity_score), ...] 높은 순.
    """
    try:
        source = MatchupProblem.objects.get(id=problem_id, tenant_id=tenant_id)
    except MatchupProblem.DoesNotExist:
        return []

    if not source.embedding:
        return []

    candidates = (
        MatchupProblem.objects
        .filter(tenant_id=tenant_id, embedding__isnull=False)
        .exclude(id=problem_id)
        .only("id", "number", "embedding", "document_id", "text",
              "source_type", "source_lecture_title", "source_session_title", "source_exam_title")
    )

    scored = []
    for c in candidates:
        if not c.embedding:
            continue
        sim = cosine_similarity(source.embedding, c.embedding)
        scored.append((c, sim))

    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:top_k]


def delete_document_with_r2(document: MatchupDocument) -> None:
    """문서 + 하위 문제 이미지 + 원본 R2 파일 모두 삭제."""
    r2_keys = [document.r2_key]
    problem_keys = list(
        document.problems.exclude(image_key="").values_list("image_key", flat=True)
    )
    r2_keys.extend(problem_keys)

    if delete_object_r2_storage:
        for key in r2_keys:
            if key:
                try:
                    delete_object_r2_storage(key=key)
                except Exception:
                    logger.warning("R2 delete failed: %s", key, exc_info=True)

    document.delete()  # CASCADE로 problems도 삭제


def retry_document(document: MatchupDocument) -> str:
    """실패한 문서를 재처리. 새 AI job을 디스패치하고 job_id 반환."""
    from apps.domains.ai.gateway import dispatch_job
    from apps.infrastructure.storage.r2 import generate_presigned_get_url_storage

    # 기존 문제 삭제
    document.problems.all().delete()

    download_url = generate_presigned_get_url_storage(
        key=document.r2_key, expires_in=3600
    )

    result = dispatch_job(
        job_type="matchup_analysis",
        payload={
            "download_url": download_url,
            "tenant_id": str(document.tenant_id),
            "document_id": str(document.id),
            "filename": document.original_name,
        },
        tenant_id=str(document.tenant_id),
        source_domain="matchup",
        source_id=str(document.id),
    )

    if isinstance(result, dict) and not result.get("ok", True):
        raise RuntimeError(result.get("error", "dispatch failed"))

    job_id = result.get("job_id", "") if isinstance(result, dict) else str(result)
    document.status = "processing"
    document.ai_job_id = str(job_id)
    document.error_message = ""
    document.problem_count = 0
    document.save(update_fields=["status", "ai_job_id", "error_message", "problem_count", "updated_at"])

    return job_id
