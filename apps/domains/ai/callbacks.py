from apps.shared.contracts.ai_result import AIResult
from apps.domains.ai.models import AIJobModel, AIResultModel
from apps.domains.submissions.services.ai_result_mapper import apply_ai_result
from apps.domains.results.tasks.grading_tasks import grade_submission_task


def handle_ai_result(result: AIResult) -> None:
    """
    Worker → API callback entry
    """
    job = AIJobModel.objects.get(job_id=result.job_id)

    if result.status == "FAILED":
        job.status = "FAILED"
        job.error_message = result.error or ""
        job.save(update_fields=["status", "error_message"])
        return

    # 1️⃣ AI 결과 저장 (fact)
    AIResultModel.objects.create(
        job=job,
        payload=result.result,
    )

    # 2️⃣ submissions 로 위임 (답안 중간산물 저장)
    submission_id = apply_ai_result(result.result)

    # 3️⃣ 채점 job enqueue (results 책임)
    if submission_id:
        grade_submission_task.delay(int(submission_id))

    job.status = "DONE"
    job.save(update_fields=["status"])
