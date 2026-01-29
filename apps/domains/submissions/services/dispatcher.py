# apps/domains/submissions/services/dispatcher.py
from __future__ import annotations

import redis
import json
from django.conf import settings

from apps.domains.submissions.models import Submission
from apps.domains.submissions.services.submission_service import SubmissionService
from apps.domains.results.tasks.grading_tasks import grade_submission_task
from apps.shared.contracts.ai_job import AIJob
from apps.infrastructure.storage.r2 import generate_presigned_get_url

# ✅ exams 도메인에서 문항 메타 제공
from apps.domains.exams.models import ExamQuestion, Sheet
from apps.domains.assets.omr.services.meta_generator import build_objective_template_meta

AI_QUEUE_KEY = "ai:jobs"


def _redis():
    return redis.from_url(settings.REDIS_URL, decode_responses=True)


def dispatch_submission(submission: Submission) -> None:
    """
    Submission 생성 직후 호출되는 유일한 진입점
    """

    # ✅ idempotency 보호
    if submission.status in (
        Submission.Status.DISPATCHED,
        Submission.Status.EXTRACTING,
        Submission.Status.ANSWERS_READY,
        Submission.Status.GRADING,
        Submission.Status.DONE,
    ):
        return

    # 1) ONLINE 제출
    if submission.source == Submission.Source.ONLINE:
        SubmissionService.process(submission)
        grade_submission_task.delay(int(submission.id))
        return

    # 2) FILE 제출
    if not submission.file_key:
        submission.status = Submission.Status.FAILED
        submission.error_message = "file_key missing"
        submission.save(update_fields=["status", "error_message"])
        return

    submission.status = Submission.Status.DISPATCHED
    submission.error_message = ""
    submission.save(update_fields=["status", "error_message"])

    job = AIJob.new(
        type=_infer_ai_job_type(submission),
        source_domain="submissions",
        source_id=str(submission.id),
        payload=_build_ai_payload(submission),
    )

    r = _redis()
    r.lpush(AI_QUEUE_KEY, job.to_json())


def _infer_ai_job_type(submission: Submission) -> str:
    if submission.source == Submission.Source.OMR_SCAN:
        return "omr_grading"
    if submission.source == Submission.Source.HOMEWORK_IMAGE:
        return "ocr"
    if submission.source == Submission.Source.HOMEWORK_VIDEO:
        return "homework_video_analysis"
    return "ocr"


def _build_ai_payload(submission: Submission) -> dict:
    payload = dict(submission.payload or {})

    # mode normalize
    mode = str(payload.get("mode") or "auto").lower()
    if mode not in ("scan", "photo", "auto"):
        mode = "auto"

    sheet_id = None
    if isinstance(payload.get("sheet_id"), int):
        sheet_id = int(payload["sheet_id"])
    elif payload.get("sheet_id") is not None:
        try:
            sheet_id = int(payload.get("sheet_id"))
        except Exception:
            sheet_id = None

    # -------------------------------------------------
    # 문항 목록 구성
    # -------------------------------------------------
    questions_payload = []
    if sheet_id:
        qs = ExamQuestion.objects.filter(sheet_id=sheet_id).order_by("number")
        for q in qs:
            region_meta = getattr(q, "region_meta", None) or getattr(q, "meta", None) or None

            questions_payload.append(
                {
                    "exam_question_id": int(q.id),
                    "number": int(getattr(q, "number", 0) or 0),
                    "region_meta": region_meta,
                }
            )

    payload.update(
        {
            "submission_id": submission.id,
            "target_type": submission.target_type,
            "target_id": submission.target_id,
            "file_key": submission.file_key,
            "download_url": generate_presigned_get_url(
                key=submission.file_key,
                expires_in=3600,
            ),
            "omr": {"sheet_id": sheet_id},
            "questions": questions_payload,
        }
    )

    # -------------------------------------------------
    # ✅ OMR v1 meta injection
    # -------------------------------------------------
    if submission.source == Submission.Source.OMR_SCAN and sheet_id:
        try:
            sh = Sheet.objects.filter(id=int(sheet_id)).first()
            qc = int(getattr(sh, "total_questions", 0) or 0)
        except Exception:
            qc = 0

        if qc in (10, 20, 30):
            try:
                payload["question_count"] = qc
                payload["mode"] = mode
                payload["template_meta"] = build_objective_template_meta(question_count=qc)
            except Exception:
                payload["mode"] = mode
        else:
            payload["mode"] = mode

    return payload
