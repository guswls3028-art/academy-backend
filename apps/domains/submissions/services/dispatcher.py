# apps/domains/submissions/services/dispatcher.py
from __future__ import annotations

import redis
from django.conf import settings

from apps.domains.submissions.models import Submission
from apps.domains.submissions.services.submission_service import SubmissionService
from apps.domains.results.tasks.grading_tasks import grade_submission_task
from apps.shared.contracts.ai_job import AIJob

# ⭐ STEP 2: presigned URL 생성 유틸
from apps.infrastructure.storage.r2 import generate_presigned_get_url

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
    Submission 생성 직후 호출되는 단일 진입점 (STEP 2 확정판)

    역할:
    - ONLINE 제출:
        - 즉시 처리 (정규화)
        - 채점 task enqueue
    - FILE 제출:
        - R2에 저장된 file_key 존재 여부만 검증
        - presigned GET URL 생성
        - AIJob enqueue
        - 파일 접근/다운로드는 worker 책임
    """

    # 1️⃣ ONLINE 제출
    if submission.source == Submission.Source.ONLINE:
        SubmissionService.process(submission)
        grade_submission_task.delay(int(submission.id))
        return

    # 2️⃣ FILE 제출 (R2 기준)
    if not submission.file_key:
        submission.status = Submission.Status.FAILED
        submission.error_message = "file_key missing"
        submission.save(update_fields=["status", "error_message"])
        return

    # 상태 전이: DISPATCHED
    submission.status = Submission.Status.DISPATCHED
    submission.error_message = ""
    submission.save(update_fields=["status", "error_message"])

    # 3️⃣ AI Job 생성 (STEP 2: presigned URL 포함)
    job = AIJob.new(
        type=_infer_ai_job_type(submission),
        payload=_build_ai_payload(submission),
        source_domain="submissions",
        source_id=str(submission.id),
    )

    # 4️⃣ Redis enqueue
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
    STEP 2 payload 규칙 (🔥 중요)

    - 로컬 파일 경로(.path) ❌ 절대 사용 금지
    - R2 presigned GET URL만 전달
    - worker는 download_url → /tmp 저장 후 처리
    """
    payload = dict(submission.payload or {})

    # ⭐ presigned GET URL 생성
    download_url = generate_presigned_get_url(
        key=submission.file_key,
        expires_in=60 * 60,  # 1시간
    )

    payload.update(
        {
            # 메타 정보
            "file_key": submission.file_key,
            "file_type": submission.file_type,

            # ⭐ worker 전용 파일 접근 수단
            "download_url": download_url,
        }
    )

    return payload
