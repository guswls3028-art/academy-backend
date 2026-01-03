# apps/domains/progress/dispatcher.py
from apps.domains.progress.tasks.progress_pipeline_task import (
    run_progress_pipeline_task,
)


def dispatch_progress_pipeline(submission_id: int) -> None:
    """
    Results → Progress 진입점
    """
    run_progress_pipeline_task.delay(submission_id)
