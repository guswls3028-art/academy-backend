# apps/domains/submissions/services/dispatcher.py
from __future__ import annotations

import redis
from django.conf import settings

from apps.domains.submissions.models import Submission
from apps.domains.submissions.services.submission_service import SubmissionService
from apps.domains.results.tasks.grading_tasks import grade_submission_task
from apps.shared.contracts.ai_job import AIJob


# ---------------------------------------------------------------------
# Redis AI Queue
# ---------------------------------------------------------------------

AI_QUEUE_KEY = "ai:jobs"


def _redis():
    return redis.from_url(settings.REDIS_URL, decode_responses=True)


# ---------------------------------------------------------------------
# Public Entry
# ---------------------------------------------------------------------

def dispatch_submission(submission: Submission) -> None:
    """
    Submission 생성 직후 호출되는 단일 진입점 (확정판)

    역할:
    - ONLINE 제출: 즉시 처리 + 채점
    - FILE 제출:
        - AIJob 생성
        - Redis AI Queue enqueue
        - 여기서는 결과 대기 ❌
        - 결과 반영/채점은 AI Worker → API 콜백에서 처리
    """

    # 1️⃣ ONLINE 제출
    if submission.source == Submission.Source.ONLINE:
        SubmissionService.process(submission)
        grade_submission_task.delay(int(submission.id))
        return

    # 2️⃣ FILE 기반 제출 (AI 필요)
    if not submission.file:
        submission.status = Submission.Status.FAILED
        submission.error_message = "file is required"
        submission.save(update_fields=["status", "error_message"])
        return

    # 상태 전이: DISPATCHED
    submission.status = Submission.Status.DISPATCHED
    submission.error_message = ""
    submission.save(update_fields=["status", "error_message"])

    # 3️⃣ AI Job 생성 (Contract only)
    job = AIJob.new(
        type=_infer_ai_job_type(submission),
        payload=_build_ai_payload(submission),
        source_domain="submissions",
        source_id=str(submission.id),
    )

    # 4️⃣ Redis enqueue (🔥 핵심)
    r = _redis()
    r.lpush(AI_QUEUE_KEY, job.to_json())


# ---------------------------------------------------------------------
# AI Job 타입 / payload 빌더
# ---------------------------------------------------------------------

def _infer_ai_job_type(submission: Submission) -> str:
    if submission.source == Submission.Source.OMR_SCAN:
        return "omr_grading"
    if submission.source == Submission.Source.HOMEWORK_IMAGE:
        return "ocr"
    if submission.source == Submission.Source.HOMEWORK_VIDEO:
        return "homework_video_analysis"
    return "ocr"


def _build_ai_payload(submission: Submission) -> dict:
    """
    Worker는 DB를 모르므로
    - file path
    - 최소 메타(payload)
    만 전달
    """
    payload = dict(submission.payload or {})

    if not submission.file:
        return payload

    if submission.source == Submission.Source.HOMEWORK_VIDEO:
        payload["video_path"] = submission.file.path

    else:
        payload["image_path"] = submission.file.path

        # OMR 필수 payload
        if submission.source == Submission.Source.OMR_SCAN:
            payload["questions"] = payload.get("questions", [])

    return payload
