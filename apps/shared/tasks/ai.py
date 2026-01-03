# # apps/shared/tasks/ai.py
# from __future__ import annotations

# from celery import shared_task
# from django.db import transaction

# from apps.domains.submissions.models import Submission
# from apps.domains.submissions.services.ai_result_mapper import apply_ai_result
# from apps.domains.results.tasks.grading_tasks import grade_submission_task


# @shared_task(bind=True, autoretry_for=(Exception,), retry_kwargs={"max_retries": 3, "countdown": 10})
# def process_ai_submission_task(self, submission_id: int) -> None:
#     """
#     MVP용 AI 처리 태스크
#     - 실제 AI 대신 payload 기반 처리 / 더미 가능
#     - 결과는 반드시 apply_ai_result로 반영
#     """
#     submission = Submission.objects.get(id=submission_id)

#     # 🔧 MVP: 실제로는 worker AI가 payload를 만든다고 가정
#     # 지금은 예시 더미
#     fake_result = {
#         "submission_id": submission.id,
#         "items": [
#             {
#                 "question_id": 1,
#                 "answer": "B",
#                 "meta": {"via": "mvp-ai"},
#             }
#         ],
#     }

#     with transaction.atomic():
#         returned_submission_id = apply_ai_result(fake_result)

#     # 답안이 생겼으면 grading으로
#     if returned_submission_id:
#         grade_submission_task.delay(returned_submission_id)




# ⚠️ process_ai_submission_task 는 MVP 더미였음
# 이제 사용 안 함
