# PATH: apps/domains/submissions/services/dispatcher.py
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from django.db import transaction

from apps.domains.submissions.models import Submission
from apps.domains.submissions.services.submission_service import SubmissionService
from apps.domains.submissions.services.transition import transit_save

from apps.domains.exams.models import ExamQuestion, Sheet
from apps.domains.assets.omr.services.meta_generator import build_objective_template_meta
from apps.infrastructure.storage.r2 import generate_presigned_get_url

from apps.domains.ai.gateway import dispatch_job
from apps.domains.results.services.grading_service import grade_submission
from apps.domains.progress.dispatcher import dispatch_progress_pipeline

# ✅ [추가] AI 워커 EC2 제어
from apps.domains.ai.services.worker_instance_control import start_ai_worker_instance

logger = logging.getLogger(__name__)


def _infer_ai_job_type(submission: Submission) -> str:
    if submission.source == Submission.Source.OMR_SCAN:
        return "omr_grading"
    if submission.source == Submission.Source.HOMEWORK_IMAGE:
        return "ocr"
    if submission.source == Submission.Source.HOMEWORK_VIDEO:
        return "homework_video_analysis"
    return "ocr"


def _safe_int(v: Any) -> Optional[int]:
    try:
        return int(v) if v is not None else None
    except Exception:
        return None


def _build_ai_payload(submission: Submission) -> Dict[str, Any]:
    payload = dict(submission.payload or {})

    mode = str(payload.get("mode") or "auto").lower()
    if mode not in ("scan", "photo", "auto"):
        mode = "auto"

    sheet_id = _safe_int(payload.get("sheet_id"))

    # sheet_id가 없으면 exam에서 자동 탐색
    if not sheet_id and submission.target_type == "exam":
        from apps.domains.exams.models import Exam
        exam = Exam.objects.filter(id=int(submission.target_id)).first()
        if exam:
            sheet = Sheet.objects.filter(exam=exam).first()
            if not sheet and getattr(exam, "template_exam_id", None):
                sheet = Sheet.objects.filter(exam_id=exam.template_exam_id).first()
            if sheet:
                sheet_id = sheet.id

    questions_payload = []
    if sheet_id:
        qs = ExamQuestion.objects.filter(sheet_id=sheet_id).order_by("number")
        for q in qs:
            region_meta = getattr(q, "region_meta", None) or getattr(q, "meta", None)
            questions_payload.append(
                {
                    "exam_question_id": int(q.id),
                    "number": int(getattr(q, "number", 0) or 0),
                    "region_meta": region_meta,
                }
            )

    download_url = None
    if submission.file_key:
        download_url = generate_presigned_get_url(
            key=submission.file_key,
            expires_in=3600,
        )

    payload.update(
        {
            "submission_id": int(submission.id),
            "target_type": submission.target_type,
            "target_id": int(submission.target_id),
            "file_key": submission.file_key,
            "download_url": download_url,
            "omr": {"sheet_id": sheet_id},
            "questions": questions_payload,
            "mode": mode,
        }
    )

    if submission.source == Submission.Source.OMR_SCAN and sheet_id:
        qc = 0
        sh = Sheet.objects.filter(id=sheet_id).first()
        if sh:
            qc = int(getattr(sh, "total_questions", 0) or 0)

        # v10: 모든 문항수에 template_meta 전달 (기존 10/20/30 제한 제거)
        if qc > 0:
            payload["question_count"] = qc
            payload["template_meta"] = build_objective_template_meta(question_count=qc)

    return payload


@transaction.atomic
def dispatch_submission(submission: Submission) -> None:
    """
    Submission 처리 SSOT.
    select_for_update로 동시 dispatch를 방지한다.
    """
    # 동시성 방어: DB에서 최신 상태를 잠금 재조회
    submission = Submission.objects.select_for_update().get(pk=submission.pk)

    if submission.status != Submission.Status.SUBMITTED:
        return

    # ONLINE
    if submission.source == Submission.Source.ONLINE:
        SubmissionService.process(submission)
        # process()가 ANSWERS_READY로 전이함. 이어서 GRADING으로.

        transit_save(submission, Submission.Status.GRADING, actor="dispatcher.online")

        grade_submission(int(submission.id))
        dispatch_progress_pipeline(submission_id=submission.id)

        transit_save(submission, Submission.Status.DONE, actor="dispatcher.online")
        return

    # FILE 기반
    if not submission.file_key:
        transit_save(
            submission, Submission.Status.FAILED,
            error_message="file_key missing",
            actor="dispatcher.file",
        )
        return

    transit_save(submission, Submission.Status.DISPATCHED, actor="dispatcher.file")

    # ==================================================
    # ✅ 1) AI Job DB 레코드 생성 + SQS 발행 + 워커 기동
    # SQS publish와 워커 기동은 transaction.on_commit()으로 실행하여
    # DB commit 이전에 워커가 메시지를 받는 race condition을 방지한다.
    # ==================================================
    job_type = _infer_ai_job_type(submission)
    payload = _build_ai_payload(submission)
    source_id = str(submission.id)

    dispatch_job(
        job_type=job_type,
        payload=payload,
        source_domain="submissions",
        source_id=source_id,
        # Note: dispatch_job 내부에서 DB에 AIJob INSERT + SQS publish가 실행된다.
        # SQS publish는 safe_dispatch 안에서 실행되므로 transaction.atomic 내부이다.
        # on_commit은 gateway 레벨에서 처리해야 하지만, 현재 구조에서는 dispatch_job이
        # 자체적으로 SQS publish를 한다. 아래 on_commit에서 워커 기동만 처리.
    )

    # 워커 기동은 반드시 DB commit 후에 (on_commit)
    def _start_worker():
        try:
            start_ai_worker_instance()
        except Exception:
            logger.warning("[dispatcher] AI 워커 EC2 기동 실패 — job은 SQS에 정상 등록됨, 워커 수동 확인 필요")
    transaction.on_commit(_start_worker)
