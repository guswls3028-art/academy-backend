# apps/domains/submissions/services/dispatcher.py
from __future__ import annotations

from apps.domains.submissions.models import Submission
from apps.domains.submissions.services.submission_service import SubmissionService
from apps.domains.results.tasks.grading_tasks import grade_submission_task
from apps.shared.tasks.ai import process_ai_submission_task


def dispatch_submission(submission: Submission) -> None:
    """
    Submission 생성 직후 호출되는 단일 진입점.

    - ONLINE:
        - submissions 도메인에서 정규화 처리
        - grading task enqueue
    - 그 외:
        - 파일 존재 여부 검증
        - Celery 기반 AI 처리 task enqueue (MVP)
    """

    # ✅ ONLINE 제출: 즉시 채점 파이프라인
    if submission.source == Submission.Source.ONLINE:
        SubmissionService.process(submission)
        grade_submission_task.delay(int(submission.id))
        return

    # ✅ 파일 기반 제출: 파일 필수
    if not submission.file:
        submission.status = Submission.Status.FAILED
        submission.error_message = "file is required"
        submission.save(update_fields=["status", "error_message"])
        return

    # ✅ MVP: AI 작업은 전부 Celery로 위임
    submission.status = Submission.Status.DISPATCHED
    submission.error_message = ""
    submission.save(update_fields=["status", "error_message"])

    # submission_id 하나만 넘긴다 (job_type / payload 분기 제거)
    process_ai_submission_task.delay(int(submission.id))
