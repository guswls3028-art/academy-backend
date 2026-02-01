# apps/domains/results/services/grading_service.py
from __future__ import annotations

from typing import Optional

from django.db import transaction

from apps.domains.results.models import ExamResult
from apps.domains.results.services.exam_grading_service import ExamGradingService


@transaction.atomic
def grade_submission(submission_id: int) -> ExamResult:
    """
    Single public grading entrypoint (SSOT).

    - queue-less 환경에서도 shell/HTTP에서 한 방에 호출 가능해야 한다.
    - 내부적으로 objective grading 수행 후 ExamResult를 반환한다.
    """
    service = ExamGradingService()
    result = service.auto_grade_objective(submission_id=int(submission_id))
    # 필요하면 여기서 finalize 정책을 넣을 수 있음(운영 정책에 따라)
    # result = service.finalize(submission_id=int(submission_id))
    return result
