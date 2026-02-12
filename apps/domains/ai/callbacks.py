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
    - 결과 fact(AIResultModel)는 API 계층에서 이미 저장되므로 여기서 생성/수정하지 않는다.
    """
    # callbacks가 job 존재를 전제로 동작 (gateway에서 job row를 선생성)
    AIJobModel.objects.get(job_id=result.job_id)

    if result.status == "FAILED":
        # 실패 상태/에러/재시도는 SQS Queue에서 처리됨
        return

    # DONE: submissions 로 위임 (답안 중간산물 저장)
    payload = result.result if isinstance(result.result, dict) else {}
    submission_id = apply_ai_result(payload)

    # 채점 실행 (Celery 제거됨, 동기 실행)
    if submission_id:
        grade_submission_task(int(submission_id))
