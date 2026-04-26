# PATH: apps/worker/ai_worker/ai/pipelines/matchup_search_qna.py
# 학생 Q&A 사진 → OCR → 임베딩 → 매치업 DB 검색
"""
payload: {post_id, attachment_id, r2_key, tenant_id}
1. R2에서 이미지 다운로드
2. 전체 이미지 OCR
3. OCR 텍스트 → 임베딩
4. MatchupProblem DB에서 cosine_similarity 검색 (top 5)
5. 결과 반환
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict

from apps.shared.contracts.ai_job import AIJob
from apps.shared.contracts.ai_result import AIResult

logger = logging.getLogger(__name__)


def run_matchup_search_qna(
    *,
    job: AIJob,
    local_path: str,
    payload: Dict[str, Any],
    tenant_id: str | None,
    record_progress: Callable,
) -> AIResult:
    job_id = str(job.id)
    post_id = payload.get("post_id", "")

    # Step 1: OCR
    record_progress(
        job_id, "ocr", 30,
        step_index=1, step_total=3,
        step_name_display="텍스트 추출",
        step_percent=0, tenant_id=tenant_id,
    )

    try:
        from apps.worker.ai_worker.ai.ocr.google import google_ocr
    except ImportError:
        return AIResult.failed(job_id, "OCR not available")

    try:
        ocr_result = google_ocr(local_path)
        text = ocr_result.text if hasattr(ocr_result, "text") else ""
    except Exception:
        logger.warning("QnA matchup OCR failed for job %s", job_id, exc_info=True)
        text = ""

    record_progress(
        job_id, "ocr", 40,
        step_index=1, step_total=3,
        step_name_display="텍스트 추출",
        step_percent=100, tenant_id=tenant_id,
    )

    if not text.strip():
        return AIResult.done(job_id, {
            "post_id": post_id,
            "results": [],
            "ocr_text": "",
        })

    # Step 2: 임베딩
    record_progress(
        job_id, "embedding", 60,
        step_index=2, step_total=3,
        step_name_display="AI 분석",
        step_percent=0, tenant_id=tenant_id,
    )

    from apps.worker.ai_worker.ai.embedding.service import get_embeddings

    try:
        batch = get_embeddings([text])
        query_embedding = batch.vectors[0] if batch.vectors else None
    except Exception:
        logger.warning("QnA matchup embedding failed for job %s", job_id, exc_info=True)
        query_embedding = None

    record_progress(
        job_id, "embedding", 70,
        step_index=2, step_total=3,
        step_name_display="AI 분석",
        step_percent=100, tenant_id=tenant_id,
    )

    if not query_embedding:
        return AIResult.done(job_id, {
            "post_id": post_id,
            "results": [],
            "ocr_text": text,
        })

    # Step 3: DB 검색 (Django ORM)
    record_progress(
        job_id, "search", 90,
        step_index=3, step_total=3,
        step_name_display="유사 문제 검색",
        step_percent=0, tenant_id=tenant_id,
    )

    # django.setup()은 워커 entrypoint(ai_sqs_worker.run_ai_sqs_worker / __main__)에서
    # 1회 호출. 함수마다 재호출하면 매번 apps registry 재로드로 불필요한 비용 + 멀티스레드
    # 환경에서 race 리스크. 여기서는 모델 import만 lazy 처리.
    from apps.domains.matchup.models import MatchupProblem
    from apps.shared.utils.vector import cosine_similarity

    candidates = (
        MatchupProblem.objects
        .filter(tenant_id=int(tenant_id), embedding__isnull=False)
        .only("id", "number", "text", "image_key", "document_id",
              "source_type", "source_exam_id", "source_question_number",
              "source_lecture_title", "source_session_title", "source_exam_title")
    )

    scored = []
    for c in candidates:
        if not c.embedding:
            continue
        sim = cosine_similarity(query_embedding, c.embedding)
        scored.append((c, sim))

    scored.sort(key=lambda x: x[1], reverse=True)
    top_results = scored[:5]

    results = []
    for problem, sim in top_results:
        results.append({
            "problem_id": problem.id,
            "similarity": round(sim, 4),
            "text": (problem.text or "")[:200],
            "image_key": problem.image_key or "",
            "number": problem.number,
            "document_id": problem.document_id,
            "source_type": problem.source_type,
            "source_lecture_title": problem.source_lecture_title or "",
            "source_session_title": problem.source_session_title or "",
            "source_exam_title": problem.source_exam_title or "",
            "source_question_number": problem.source_question_number,
        })

    record_progress(
        job_id, "done", 100,
        step_index=3, step_total=3,
        step_name_display="완료",
        step_percent=100, tenant_id=tenant_id,
    )

    return AIResult.done(job_id, {
        "post_id": post_id,
        "results": results,
        "ocr_text": text[:500],
    })
