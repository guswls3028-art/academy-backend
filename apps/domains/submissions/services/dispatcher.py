from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from django.db import transaction

from apps.domains.submissions.models import Submission
from apps.domains.submissions.services.submission_service import SubmissionService
from apps.domains.exams.models import ExamQuestion, Sheet
from apps.domains.assets.omr.services.meta_generator import build_objective_template_meta
from apps.infrastructure.storage.r2 import generate_presigned_get_url
from apps.domains.ai.gateway import dispatch_job
from apps.domains.results.services.grading_service import grade_submission
from apps.domains.progress.dispatcher import dispatch_progress_pipeline

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
    sheet_id = _safe_int(payload.get("sheet_id"))

    questions = []
    if sheet_id:
        for q in ExamQuestion.objects.filter(sheet_id=sheet_id).order_by("number"):
            questions.append(
                {
                    "exam_question_id": q.id,
                    "number": q.number,
                    "region_meta": getattr(q, "region_meta", None),
                }
            )

    download_url = (
        generate_presigned_get_url(submission.file_key, 3600)
        if submission.file_key
        else None
    )

    payload.update(
        {
            "submission_id": submission.id,
            "target_type": submission.target_type,
            "target_id": submission.target_id,
            "file_key": submission.file_key,
            "download_url": download_url,
            "questions": questions,
        }
    )

    if submission.source == Submission.Source.OMR_SCAN and sheet_id:
        sh = Sheet.objects.filter(id=sheet_id).first()
        if sh and sh.total_questions in (10, 20, 30):
            payload["question_count"] = sh.total_questions
            payload["template_meta"] = build_objective_template_meta(
                question_count=sh.total_questions
            )

    return payload


@transaction.atomic
def dispatch_submission(submission: Submission) -> None:
    if submission.status != Submission.Status.SUBMITTED:
        return

    if submission.source == Submission.Source.ONLINE:
        SubmissionService.process(submission)
        submission.status = Submission.Status.GRADING
        submission.save(update_fields=["status", "updated_at"])
        grade_submission(submission.id)
        dispatch_progress_pipeline(submission_id=submission.id)
        submission.status = Submission.Status.DONE
        submission.save(update_fields=["status", "updated_at"])
        return

    if not submission.file_key:
        submission.status = Submission.Status.FAILED
        submission.error_message = "file_key missing"
        submission.save(update_fields=["status", "error_message", "updated_at"])
        return

    submission.status = Submission.Status.DISPATCHED
    submission.error_message = ""
    submission.save(update_fields=["status", "error_message", "updated_at"])

    dispatch_job(
        job_type=_infer_ai_job_type(submission),
        payload=_build_ai_payload(submission),
        source_domain="submissions",
        source_id=str(submission.id),
    )
