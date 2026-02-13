# apps/domains/ai/callbacks.py
from apps.shared.contracts.ai_result import AIResult
from apps.domains.ai.models import AIJobModel
from apps.domains.submissions.services.ai_omr_result_mapper import apply_ai_result
from apps.domains.results.tasks.grading_tasks import grade_submission_task


def handle_ai_result(result: AIResult) -> None:
    """
    Worker → API callback entry

    규칙(무퇴화/정규화):
    - 상태(status) 변경은 Queue/API(SQS Queue)가 SSOT로 처리한다.
    - callbacks는 "도메인 후속 처리"만 담당한다.
    - Lite/Basic 실패 없음: 워커가 FAILED를 보내도 job은 이미 View에서 DONE으로 저장됐을 수 있음.
        이 경우 result.status는 여전히 FAILED이므로, job.tier가 lite/basic이면 DONE으로 간주하고
        submission 쪽 적용을 진행한다.
    """
    job = AIJobModel.objects.filter(job_id=result.job_id).first()
    if not job:
        return

    raw_payload = result.result if isinstance(result.result, dict) else {}

    # 워커가 FAILED를 보냈을 때: Lite/Basic이면 DONE으로 간주하고 submission 적용
    if result.status == "FAILED":
        tier = (job.tier or "basic").lower()
        if tier in ("lite", "basic"):
            payload = {
                "submission_id": raw_payload.get("submission_id"),
                "status": "DONE",
                "result": raw_payload,
                "error": None,
            }
            submission_id = apply_ai_result(payload)
            if submission_id:
                grade_submission_task(int(submission_id))
        return

    # DONE: submissions 로 위임 (답안 중간산물 저장)
    submission_id = apply_ai_result(raw_payload)

    if submission_id:
        grade_submission_task(int(submission_id))
