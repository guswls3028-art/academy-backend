# apps/domains/results/tasks/grading_tasks.py
from celery import shared_task

from apps.domains.submissions.models import Submission
from apps.domains.results.services.grader import grade_submission_to_results


@shared_task(bind=True, autoretry_for=(Exception,), retry_kwargs={"max_retries": 3})
def grade_submission_task(self, submission_id: int) -> bool:
    submission = Submission.objects.get(id=submission_id)
    grade_submission_to_results(submission)
    return True
