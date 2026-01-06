# apps/domains/submissions/services/dispatcher.py
from __future__ import annotations

import redis
from django.conf import settings

from apps.domains.submissions.models import Submission
from apps.domains.submissions.services.submission_service import SubmissionService
from apps.domains.results.tasks.grading_tasks import grade_submission_task
from apps.shared.contracts.ai_job import AIJob
from apps.infrastructure.storage.r2 import generate_presigned_get_url

# ✅ exams 도메인에서 문항 메타 제공
from apps.domains.exams.models import ExamQuestion

AI_QUEUE_KEY = "ai:jobs"


def _redis():
    return redis.from_url(settings.REDIS_URL, decode_responses=True)


def dispatch_submission(submission: Submission) -> None:
    """
    Submission 생성 직후 호출되는 유일한 진입점

    상태 전이 규칙 (고정):
    - SUBMITTED → DISPATCHED : dispatcher
    - DISPATCHED → ANSWERS_READY / FAILED : ai_result_mapper
    - ANSWERS_READY → GRADING → DONE : results.grader
    """

    # 1) ONLINE 제출: 즉시 처리
    if submission.source == Submission.Source.ONLINE:
        SubmissionService.process(submission)
        grade_submission_task.delay(int(submission.id))
        return

    # 2) FILE 제출: presigned URL → AI Worker
    if not submission.file_key:
        submission.status = Submission.Status.FAILED
        submission.error_message = "file_key missing"
        submission.save(update_fields=["status", "error_message"])
        return

    # 상태 전이: SUBMITTED → DISPATCHED
    submission.status = Submission.Status.DISPATCHED
    submission.error_message = ""
    submission.save(update_fields=["status", "error_message"])

    # 3) AI Job 생성
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
    """
    ✅ NEXT-2 확정 payload 규칙
    - file 접근은 presigned GET URL만
    - OMR은 "sheet_id" 기반으로 문항 목록을 함께 제공
    - worker는 answers[*].exam_question_id 로만 결과를 리턴해야 함

    Worker 입력(권장):
    {
      "submission_id": ...,
      "download_url": "...",
      "omr": {"sheet_id": 45},
      "questions": [
        {
          "exam_question_id": 123,
          "number": 1,
          "region_meta": {...}  # bbox 등
        },
        ...
      ]
    }
    """
    payload = dict(submission.payload or {})

    sheet_id = None
    if isinstance(payload.get("sheet_id"), int):
        sheet_id = int(payload["sheet_id"])
    elif payload.get("sheet_id") is not None:
        try:
            sheet_id = int(payload.get("sheet_id"))
        except Exception:
            sheet_id = None

    # -------------------------------------------------
    # ✅ 문항 목록 구성 (sheet_id 기반)
    # -------------------------------------------------
    questions_payload = []
    if sheet_id:
        qs = ExamQuestion.objects.filter(sheet_id=sheet_id).order_by("number")
        for q in qs:
            # region_meta 필드명이 프로젝트마다 다를 수 있어 getattr로 방어
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
                expires_in=60 * 60,
            ),

            # ✅ OMR 전용
            "omr": {
                "sheet_id": sheet_id,
            },

            # ✅ NEXT-2 핵심: worker가 exam_question_id를 알도록 제공
            "questions": questions_payload,
        }
    )

    return payload
