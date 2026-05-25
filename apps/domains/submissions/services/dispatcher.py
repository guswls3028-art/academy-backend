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

# AI 워커 EC2 제어 (2026-05-12: apps/domains/ai/services -> academy/adapters/compute 이관)
from academy.adapters.compute.ec2_control import start_ai_worker_instance

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


def resolve_omr_sheet_for_exam(
    *,
    tenant,
    exam_id: int,
    requested_sheet_id: Optional[int],
) -> Sheet:
    """
    Resolve the OMR sheet for an exam with tenant/exam scoping.

    OMR coordinates are as sensitive as the answer key: using another exam's
    sheet can map bubbles to the wrong questions. Fail closed instead of
    silently falling back to a generic 30-question template.
    """
    from apps.domains.exams.models import Exam

    exam = Exam.objects.filter(
        id=int(exam_id),
        tenant=tenant,
    ).first()
    if not exam:
        raise ValueError("OMR target exam not found for tenant")

    allowed_exam_ids = {int(exam.id), int(exam.effective_template_exam_id)}
    qs = Sheet.objects.select_related("exam").filter(
        exam_id__in=allowed_exam_ids,
        exam__tenant=tenant,
    )

    if requested_sheet_id:
        sheet = qs.filter(id=int(requested_sheet_id)).first()
        if not sheet:
            raise ValueError("sheet_id does not belong to this exam")
        return sheet

    preferred = qs.filter(exam_id=int(exam.effective_template_exam_id)).first()
    sheet = preferred or qs.first()
    if not sheet:
        raise ValueError("OMR sheet not found for this exam")
    return sheet


def resolve_omr_sheet_for_submission(
    submission: Submission,
    requested_sheet_id: Optional[int],
) -> Sheet:
    if submission.target_type != Submission.TargetType.EXAM:
        raise ValueError("OMR submission target_type must be exam")
    return resolve_omr_sheet_for_exam(
        tenant=submission.tenant,
        exam_id=int(submission.target_id),
        requested_sheet_id=requested_sheet_id,
    )


def _build_ai_payload(submission: Submission) -> Dict[str, Any]:
    payload = dict(submission.payload or {})

    mode = str(payload.get("mode") or "auto").lower()
    if mode not in ("scan", "photo", "auto"):
        mode = "auto"

    raw_sheet_id = payload.get("sheet_id")
    sheet_id = _safe_int(raw_sheet_id)
    if (
        submission.source == Submission.Source.OMR_SCAN
        and raw_sheet_id not in (None, "")
        and sheet_id is None
    ):
        raise ValueError("sheet_id must be integer")
    sheet: Sheet | None = None
    if submission.source == Submission.Source.OMR_SCAN:
        sheet = resolve_omr_sheet_for_submission(submission, sheet_id)
        sheet_id = int(sheet.id)

    questions_payload = []
    if sheet_id:
        qs = ExamQuestion.objects.filter(sheet_id=int(sheet_id)).order_by("number")
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
        qc = int(getattr(sheet, "total_questions", 0) or 0) if sheet else 0

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

        # grade_submission 내부에서 auto_grade_objective가 DONE 전이 + dispatch_progress_pipeline 호출까지 수행
        grade_submission(int(submission.id))
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
    try:
        payload = _build_ai_payload(submission)
    except ValueError as e:
        transit_save(
            submission,
            Submission.Status.FAILED,
            error_message=str(e),
            actor="dispatcher.payload",
        )
        return
    source_id = str(submission.id)

    dispatch_result = dispatch_job(
        job_type=job_type,
        payload=payload,
        tenant_id=str(submission.tenant_id),
        source_domain="submissions",
        source_id=source_id,
    )
    if not dispatch_result.get("ok"):
        meta = dict(submission.meta or {})
        meta["ai_dispatch"] = {
            "ok": False,
            "job_type": job_type,
            "job_id": dispatch_result.get("job_id"),
            "rejection_code": dispatch_result.get("rejection_code"),
            "error": dispatch_result.get("error") or "AI dispatch failed",
        }
        submission.meta = meta
        transit_save(
            submission,
            Submission.Status.FAILED,
            error_message=str(dispatch_result.get("error") or "AI dispatch failed"),
            actor="dispatcher.ai_dispatch",
            extra_update_fields=["meta"],
        )
        return

    # 워커 기동은 반드시 DB commit 후에 (on_commit)
    def _start_worker():
        try:
            start_ai_worker_instance()
        except Exception:
            logger.warning("[dispatcher] AI 워커 EC2 기동 실패 — job은 SQS에 정상 등록됨, 워커 수동 확인 필요")
    transaction.on_commit(_start_worker)
