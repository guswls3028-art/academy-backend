from apps.domains.results.services.grading_service import grade_submission


def grade_submission_task(submission_id: int):
    """
    Celery 없이도 동작 가능한 grading entry
    """
    return grade_submission(submission_id)
