# PATH: apps/domains/submissions/services/dispatcher.py
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

        if qc in (10, 20, 30):
            payload["question_count"] = qc
            payload["template_meta"] = build_objective_template_meta(question_count=qc)

    return payload


@transaction.atomic
def dispatch_submission(submission: Submission) -> None:
    """
    Submission 처리 SSOT
    """

    if submission.status != Submission.Status.SUBMITTED:
        return

    # ONLINE
    if submission.source == Submission.Source.ONLINE:
        SubmissionService.process(submission)

        submission.status = Submission.Status.GRADING
        submission.save(update_fields=["status", "updated_at"])

        grade_submission(int(submission.id))
        dispatch_progress_pipeline(submission_id=submission.id)

        submission.status = Submission.Status.DONE
        submission.save(update_fields=["status", "updated_at"])
        return

    # FILE 기반
    if not submission.file_key:
        submission.status = Submission.Status.FAILED
        submission.error_message = "file_key missing"
        submission.save(update_fields=["status", "error_message", "updated_at"])
        return

    submission.status = Submission.Status.DISPATCHED
    submission.error_message = ""
    submission.save(update_fields=["status", "error_message", "updated_at"])

    # ==================================================
    # ✅ 1) AI Job 생성 (DB SSOT)
    # ==================================================
    dispatch_job(
        job_type=_infer_ai_job_type(submission),
        payload=_build_ai_payload(submission),
        source_domain="submissions",
        source_id=str(submission.id),
    )

    # ==================================================
    # ✅ 2) 워커 EC2 깨우기 (API 서버 책임)
    # - job이 실제로 생긴 뒤에만 호출
    # - 중복 호출돼도 EC2는 idempotent
    # ==================================================
    start_ai_worker_instance()
