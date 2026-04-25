# PATH: apps/domains/matchup/services.py
# 매치업 비즈니스 로직 — 유사도 검색, R2 정리, 재시도

from __future__ import annotations

import logging
import os
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
# V2 측정(15 케이스)에서 발견된 부작용으로 V2.5 보수화:
#  - format_match=0.12가 같은 시험지 essay-essay 트랩을 강화 → 0.0
#  - length_norm=0.06이 정제 후 짧아진 텍스트에 부정적 영향 → 0.0
#  - sim 비중 ↑, cross_doc만 살려 서답형 트랩 약화 (다른 시험지 우선)
# 휴리스틱은 여기까지. 80%+ 도약은 cross-encoder reranker (Phase 2)에서.
_W_SIM = 1.0         # V2.6: 휴리스틱 전부 비활성 — 직접 측정에서 휴리스틱이
_W_FORMAT = 0.0      #        top1 외에 top2/3 회복을 망침. 순수 sim으로 회귀.
_W_LENGTH = 0.0      #
_W_CROSS_DOC = 0.0   #

# Phase 2 cross-encoder 토글 (기본 OFF).
# bge-reranker-base는 한국어 시험 문제 의미를 잘 못 잡아 V2.6 56% → 40% 후퇴.
# v2-m3-ko로 재시도하려면 EBS 8GB→20GB 확장 필요.
# 운영 중 활성화: SSM에서 환경변수 MATCHUP_USE_CROSS_ENCODER=1 + ASG refresh.
_USE_CROSS_ENCODER = os.environ.get("MATCHUP_USE_CROSS_ENCODER", "0") == "1"


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
    """주어진 문제와 유사한 문제를 찾아 재정렬해 반환.

    Pipeline:
      1. bi-encoder cosine으로 후보 점수화 (DB의 embedding)
      2. 휴리스틱 신호(sim·cross_doc) 결합 → 1차 정렬
      3. (가능 시) cross-encoder reranker로 상위 후보 재정렬 — phase 2
      4. top_k 반환

    Returns: [(problem, final_score), ...] 높은 순.
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

    # 1차: bi-encoder + 가벼운 휴리스틱
    scored = []
    for c in candidates:
        if not c.embedding:
            continue
        sim = cosine_similarity(source.embedding, c.embedding)

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

    # 2차: cross-encoder reranking — 환경변수 MATCHUP_USE_CROSS_ENCODER=1 일 때만.
    # 기본 OFF: bge-reranker-base가 한국어 시험 문제에 부적합 확인됨.
    if _USE_CROSS_ENCODER:
        pre_top = scored[:max(top_k * 2, 20)]
        if len(pre_top) >= 2:
            reranked = _rerank_with_cross_encoder(source, pre_top)
            if reranked is not None:
                return reranked[:top_k]

    return scored[:top_k]


def _rerank_with_cross_encoder(source, pre_top):
    """Cross-encoder로 pre_top 재정렬. 의존성 없거나 실패 시 None.

    Returns: [(problem, score), ...] 또는 None
    """
    try:
        from . import reranker as rr
    except ImportError:
        return None
    cands_text = [(p.text or "") for p, _ in pre_top]
    rr_result = rr.rerank(source.text or "", cands_text, top_k=len(pre_top))
    if rr_result is None:
        return None
    return [(pre_top[idx][0], float(score)) for idx, score in rr_result]


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
