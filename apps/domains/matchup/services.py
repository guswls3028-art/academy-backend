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


# ── Heuristic reranker 가중치 ───────────────────────────
#
# 정밀 평가(2026-04-26, 15 케이스)에서 발견:
#  - 같은 시험지 내 다른 서답형이 sim 0.86으로 잡혀 top1 트랩
#  - 단원 다른 결과가 top1로 끼어 들어옴
# bi-encoder sim에 추가 신호를 결합해 재정렬.
_W_SIM = 0.78        # 임베딩 유사도 (주 신호)
_W_FORMAT = 0.12     # 포맷 일치 (essay vs choice) — 서답형 트랩 차단
_W_LENGTH = 0.06     # 텍스트 길이 비율 — 너무 짧거나 너무 긴 후보 페널티
_W_CROSS_DOC = 0.04  # 다른 시험지 가산 (선생님이 보충용으로 추천 받는 의도)


def _format_of(problem: MatchupProblem) -> str:
    """problem의 meta에서 format 추출. 미설정이면 텍스트로 즉석 감지(레거시)."""
    meta = problem.meta or {}
    fmt = meta.get("format")
    if fmt in ("essay", "choice"):
        return fmt
    text = problem.text or ""
    return "essay" if any(
        marker in text[:20] for marker in ("[서답형", "[ 서답형", "[서 답형", "[ 서 답형", "서논술형")
    ) else "choice"


def _length_score(src_len: int, cand_len: int) -> float:
    """텍스트 길이 비율 점수. 비슷한 길이일수록 1.0, 차이 클수록 0."""
    if src_len <= 0 or cand_len <= 0:
        return 0.5  # 정보 부족 — 중립
    short, long_ = sorted([src_len, cand_len])
    return short / long_


def find_similar_problems(
    problem_id: int, tenant_id: int, top_k: int = 10
) -> List[Tuple["MatchupProblem", float]]:
    """
    주어진 문제와 유사한 문제를 찾아 재정렬해 반환.

    1. bi-encoder cosine으로 후보 점수화
    2. 휴리스틱 reranker (format match + length norm + cross-doc) 결합
    3. 결합 점수 기준 정렬

    Returns: [(problem, final_score), ...] 높은 순.
    final_score는 sim에 reranker 가중치까지 합쳐진 값(0~1 근사 범위, 기존 의미와
    유사하도록 _W_SIM 비중을 크게 둠).
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
        .defer("created_at", "updated_at")
    )

    src_format = _format_of(source)
    src_len = len(source.text or "")
    src_doc_id = source.document_id

    scored = []
    for c in candidates:
        if not c.embedding:
            continue
        sim = cosine_similarity(source.embedding, c.embedding)

        # 휴리스틱 신호
        fmt_match = 1.0 if _format_of(c) == src_format else 0.0
        len_score = _length_score(src_len, len(c.text or ""))
        cross_doc = 1.0 if c.document_id != src_doc_id else 0.0

        final = (
            _W_SIM * sim
            + _W_FORMAT * fmt_match
            + _W_LENGTH * len_score
            + _W_CROSS_DOC * cross_doc
        )
        scored.append((c, final))

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
