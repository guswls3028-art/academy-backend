# PATH: apps/worker/ai_worker/ai/pipelines/matchup_index_exam.py
# 시험 문제 → 매치업 인덱싱 파이프라인
# ExamQuestion 이미지 → OCR → 임베딩 → MatchupProblem 생성
"""
payload: {exam_id, tenant_id}
1. DB에서 ExamQuestion 목록 조회
2. 각 문제 이미지 → presigned URL → 다운로드
3. 전체 이미지 OCR
4. OCR 텍스트 → 임베딩
5. MatchupProblem bulk_create (source_type="exam")
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict

from apps.shared.contracts.ai_job import AIJob
from apps.shared.contracts.ai_result import AIResult

logger = logging.getLogger(__name__)


def run_matchup_index_exam(
    *,
    job: AIJob,
    payload: Dict[str, Any],
    tenant_id: str | None,
    record_progress: Callable,
) -> AIResult:
    job_id = str(job.id)
    exam_id = payload.get("exam_id")

    if not exam_id:
        return AIResult.failed(job_id, "exam_id missing")

    record_progress(
        job_id, "loading", 10,
        step_index=1, step_total=4,
        step_name_display="문제 로드",
        step_percent=0, tenant_id=tenant_id,
    )

    # DB에서 ExamQuestion 조회 (Django ORM — 워커에서도 사용 가능)
    import django
    django.setup()

    from apps.domains.exams.models import Exam, ExamQuestion

    try:
        exam = Exam.objects.select_related("sheet").get(id=int(exam_id))
    except Exam.DoesNotExist:
        return AIResult.failed(job_id, f"Exam {exam_id} not found")

    # template exam의 sheet에서 문제 조회
    effective_id = exam.effective_template_exam_id
    try:
        questions = list(
            ExamQuestion.objects
            .filter(sheet__exam_id=effective_id)
            .order_by("number")
            .values("id", "number", "image_key")
        )
    except Exception as e:
        return AIResult.failed(job_id, f"Failed to load questions: {e}")

    if not questions:
        return AIResult.done(job_id, {"indexed": 0, "exam_id": str(exam_id)})

    # 출처 정보 수집
    sessions = exam.sessions.select_related("lecture").all()
    session = sessions.first()
    lecture = session.lecture if session else None

    source_info = {
        "lecture_title": lecture.title if lecture else "",
        "session_title": session.title if session else "",
        "exam_title": exam.title,
    }

    record_progress(
        job_id, "loading", 20,
        step_index=1, step_total=4,
        step_name_display="문제 로드",
        step_percent=100, tenant_id=tenant_id,
    )

    # OCR — 각 문제 이미지에서 텍스트 추출
    record_progress(
        job_id, "ocr", 30,
        step_index=2, step_total=4,
        step_name_display="텍스트 추출",
        step_percent=0, tenant_id=tenant_id,
    )

    texts = _ocr_exam_questions(questions, job_id)

    record_progress(
        job_id, "ocr", 50,
        step_index=2, step_total=4,
        step_name_display="텍스트 추출",
        step_percent=100, tenant_id=tenant_id,
    )

    # 임베딩
    record_progress(
        job_id, "embedding", 60,
        step_index=3, step_total=4,
        step_name_display="AI 분석",
        step_percent=0, tenant_id=tenant_id,
    )

    embeddings = _generate_embeddings_for_texts(texts, job_id)

    record_progress(
        job_id, "embedding", 80,
        step_index=3, step_total=4,
        step_name_display="AI 분석",
        step_percent=100, tenant_id=tenant_id,
    )

    # 결과 반환
    record_progress(
        job_id, "done", 100,
        step_index=4, step_total=4,
        step_name_display="완료",
        step_percent=100, tenant_id=tenant_id,
    )

    problems = []
    for i, q in enumerate(questions):
        problems.append({
            "number": q["number"],
            "text": texts[i] if i < len(texts) else "",
            "image_key": q["image_key"] or "",
            "embedding": embeddings[i] if i < len(embeddings) else None,
            "source_type": "exam",
            "source_exam_id": int(exam_id),
            "source_question_number": q["number"],
            **source_info,
        })

    return AIResult.done(job_id, {
        "problems": problems,
        "exam_id": str(exam_id),
        "indexed": len(problems),
    })


def _ocr_exam_questions(questions, job_id):
    """ExamQuestion 이미지에서 OCR 텍스트 추출."""
    try:
        from apps.worker.ai_worker.ai.ocr.google import google_ocr
    except ImportError:
        logger.warning("Google OCR not available for exam indexing")
        return [""] * len(questions)

    try:
        from apps.infrastructure.storage.r2 import generate_presigned_get_url_storage
    except ImportError:
        logger.warning("R2 storage not available for exam indexing")
        return [""] * len(questions)

    from apps.worker.ai_worker.storage.downloader import download_to_tmp

    texts = []
    for q in questions:
        if not q.get("image_key"):
            texts.append("")
            continue

        try:
            url = generate_presigned_get_url_storage(key=q["image_key"], expires_in=3600)
            local_path = download_to_tmp(download_url=url, job_id=job_id)
            result = google_ocr(local_path)
            texts.append(result.text if hasattr(result, "text") else "")
        except Exception:
            logger.warning("OCR failed for exam Q%d in job %s", q["number"], job_id, exc_info=True)
            texts.append("")

    return texts


def _generate_embeddings_for_texts(texts, job_id):
    """텍스트 → 임베딩."""
    from apps.worker.ai_worker.ai.embedding.service import get_embeddings

    non_empty = [(i, t) for i, t in enumerate(texts) if t.strip()]
    if not non_empty:
        return [None] * len(texts)

    try:
        batch = get_embeddings([t for _, t in non_empty])
        result = [None] * len(texts)
        for vec_idx, (orig_idx, _) in enumerate(non_empty):
            result[orig_idx] = batch.vectors[vec_idx]
        return result
    except Exception:
        logger.warning("Embedding failed for job %s", job_id, exc_info=True)
        return [None] * len(texts)
