# PATH: apps/domains/submissions/services/dispatcher.py
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from django.db import transaction

from apps.domains.submissions.models import Submission
from apps.domains.submissions.services.submission_service import SubmissionService
from apps.domains.submissions.services.lifecycle import (
    fail_submission,
    mark_dispatched,
    mark_grading,
)

from apps.support.submissions.dependencies import (
    dispatch_submission_ai_job,
    grade_submission_objective,
)

dispatch_job = dispatch_submission_ai_job

# AI 워커 EC2 제어 (2026-05-12: apps/domains/ai/services -> academy/adapters/compute 이관)
from academy.adapters.compute.ec2_control import start_ai_worker_instance

# OMR pipeline 의 sheet / payload 책임은 apps/support/omr/ 에 위치.
# (Phase F: payload / sheet 책임 분리. boundary guard 범위 외 helper layer 로 이동.)
from apps.support.omr.payload_builder import build_omr_payload
from apps.support.omr.sheet_resolver import (
    resolve_omr_sheet_for_exam,
    resolve_omr_sheet_for_submission,
)


logger = logging.getLogger(__name__)


# 기존 import 경로 호환 (외부 사용처 다수)
__all__ = [
    "dispatch_submission",
    "resolve_omr_sheet_for_exam",
    "resolve_omr_sheet_for_submission",
]


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
    """
    source 별 payload 빌드 진입점.

    - OMR_SCAN: omr_pipeline.payload_builder.build_omr_payload 위임.
    - 그 외 (HOMEWORK_IMAGE / HOMEWORK_VIDEO / ONLINE 등): generic file payload.
    """
    if submission.source == Submission.Source.OMR_SCAN:
        return build_omr_payload(submission)

    # generic: HOMEWORK_* 등
    payload = dict(submission.payload or {})
    mode = str(payload.get("mode") or "auto").lower()
    if mode not in ("scan", "photo", "auto"):
        mode = "auto"

    from apps.infrastructure.storage.r2 import generate_presigned_get_url

    download_url = None
    if submission.file_key:
        download_url = generate_presigned_get_url(
            key=submission.file_key, expires_in=3600
        )

    payload.update(
        {
            "submission_id": int(submission.id),
            "target_type": submission.target_type,
            "target_id": int(submission.target_id),
            "file_key": submission.file_key,
            "download_url": download_url,
            "mode": mode,
        }
    )
    return payload


@transaction.atomic
def dispatch_submission(submission: Submission) -> None:
    """
    Submission 처리 SSOT.

    Online 답안은 즉시 채점, file 기반(OMR / Homework)은 AI worker 로 enqueue.
    select_for_update 로 동시 dispatch 를 방지한다.
    """
    submission = Submission.objects.select_for_update().get(pk=submission.pk)

    if submission.status != Submission.Status.SUBMITTED:
        return

    # ONLINE — 즉시 채점
    if submission.source == Submission.Source.ONLINE:
        SubmissionService.process(submission)
        mark_grading(submission, actor="dispatcher.online")
        grade_submission_objective(int(submission.id))
        return

    # FILE 기반 — 파일 확인
    if not submission.file_key:
        fail_submission(
            submission,
            error_message="file_key missing",
            actor="dispatcher.file",
        )
        return

    mark_dispatched(submission, actor="dispatcher.file")

    # AI Job dispatch + 워커 기동
    job_type = _infer_ai_job_type(submission)
    try:
        payload = _build_ai_payload(submission)
    except ValueError as exc:
        fail_submission(
            submission,
            error_message=str(exc),
            actor="dispatcher.payload",
        )
        return

    dispatch_result = dispatch_job(
        job_type=job_type,
        payload=payload,
        tenant_id=str(submission.tenant_id),
        source_domain="submissions",
        source_id=str(submission.id),
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
        fail_submission(
            submission,
            error_message=str(dispatch_result.get("error") or "AI dispatch failed"),
            actor="dispatcher.ai_dispatch",
            extra_update_fields=["meta"],
        )
        return

    # SQS publish 가 DB commit 후에 안전하게 실행되도록 on_commit 사용
    def _start_worker():
        try:
            start_ai_worker_instance()
        except Exception:
            logger.warning(
                "[dispatcher] AI 워커 EC2 기동 실패 — job 은 SQS 에 정상 등록됨,"
                " 워커 수동 확인 필요"
            )

    transaction.on_commit(_start_worker)
