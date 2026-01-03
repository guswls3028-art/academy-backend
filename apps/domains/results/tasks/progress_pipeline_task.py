# apps/domains/progress/tasks/progress_pipeline_task.py
from celery import shared_task

from apps.domains.submissions.models import Submission
from apps.domains.results.models import Result
from apps.domains.progress.services.progress_pipeline import ProgressPipeline


@shared_task(bind=True, autoretry_for=(Exception,), retry_kwargs={"max_retries": 3})
def run_progress_pipeline_task(self, submission_id: int) -> bool:
    submission = Submission.objects.get(id=submission_id)

    result = Result.objects.filter(
        target_type=submission.target_type,
        target_id=submission.target_id,
        enrollment_id=submission.enrollment_id,
    ).first()

    if not result:
        return False

    ProgressPipeline.run_by_submission(
        submission=submission,
        result=result,
    )

    return True
