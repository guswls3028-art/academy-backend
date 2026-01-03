# apps/domains/submissions/services/dispatcher.py
from __future__ import annotations

from apps.domains.submissions.models import Submission
from apps.domains.submissions.services.submission_service import SubmissionService
from apps.domains.results.tasks.grading_tasks import grade_submission_task

from apps.shared.contracts.ai_job import AIJob
from apps.shared.contracts.ai_result import AIResult
from apps.shared.tasks.ai_worker import run_ai_job_task
from apps.domains.submissions.services.ai_result_mapper import apply_ai_result


def dispatch_submission(submission: Submission) -> None:
    """
    Submission 생성 직후 호출되는 단일 진입점.

    - ONLINE:
        - submissions 내부 처리
        - grading 바로 enqueue
    - FILE 기반:
        - AI Job 생성 → ai worker celery
        - (MVP) 동기 결과 수신
        - 결과 반영 → grading enqueue

    ⚠️ MVP ONLY
    - async_result.get()은 API worker를 block함
    - 추후 callback / polling 구조로 교체 예정
    """

    # 1️⃣ ONLINE 제출
    if submission.source == Submission.Source.ONLINE:
        SubmissionService.process(submission)
        grade_submission_task.delay(int(submission.id))
        return

    # 2️⃣ FILE 기반 제출
    if not submission.file:
        submission.status = Submission.Status.FAILED
        submission.error_message = "file is required"
        submission.save(update_fields=["status", "error_message"])
        return

    submission.status = Submission.Status.DISPATCHED
    submission.error_message = ""
    submission.save(update_fields=["status", "error_message"])

    # 3️⃣ AI Job 생성
    job = AIJob.new(
        type=_infer_ai_job_type(submission),
        payload=_build_ai_payload(submission),
        source_domain="submissions",
        source_id=str(submission.id),
    )

    # 4️⃣ AI Worker 실행 (MVP: 동기 대기)
    async_result = run_ai_job_task.delay(job.to_dict())

    try:
        result_dict = async_result.get(timeout=120)
    except Exception as e:
        submission.status = Submission.Status.FAILED
        submission.error_message = f"AI timeout or error: {e}"
        submission.save(update_fields=["status", "error_message"])
        return

    ai_result = AIResult.from_dict(result_dict)

    if ai_result.status != "DONE":
        submission.status = Submission.Status.FAILED
        submission.error_message = ai_result.error or "AI failed"
        submission.save(update_fields=["status", "error_message"])
        return

    # 5️⃣ AI 결과 반영
    returned_submission_id = apply_ai_result(
        {
            **ai_result.result,
            "submission_id": submission.id,
        }
    )

    # 6️⃣ 채점 enqueue
    if returned_submission_id:
        grade_submission_task.delay(returned_submission_id)


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
    Worker는 DB를 모르므로 path / 최소 정보만 전달
    """
    payload = dict(submission.payload or {})

    if not submission.file:
        return payload

    if submission.source == Submission.Source.HOMEWORK_VIDEO:
        payload["video_path"] = submission.file.path

    else:
        payload["image_path"] = submission.file.path

        # ✅ OMR 필수 payload (이거 없으면 결과 0개 나옴)
        if submission.source == Submission.Source.OMR_SCAN:
            payload["questions"] = payload.get("questions", [])

    return payload
