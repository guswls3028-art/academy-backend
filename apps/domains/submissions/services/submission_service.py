# apps/domains/submissions/services/submission_service.py
from typing import List, Type

from django.db import transaction

from apps.domains.submissions.models import Submission, SubmissionAnswer
from apps.domains.submissions.services.processor.base import BaseSubmissionProcessor
from apps.domains.submissions.services.processor.online import OnlineSubmissionProcessor


PROCESSOR_MAP: dict[str, Type[BaseSubmissionProcessor]] = {
    Submission.Source.ONLINE: OnlineSubmissionProcessor,
}


class SubmissionService:
    """
    submissions 처리의 유일한 퍼블릭 서비스
    - ONLINE만 즉시 처리 (정규화만 수행)
    # OMR / IMAGE / VIDEO는 반드시 AI Worker 경유
    """

    @staticmethod
    @transaction.atomic
    def process(submission: Submission) -> List[SubmissionAnswer]:
        processor_cls = PROCESSOR_MAP.get(submission.source)
        if not processor_cls:
            return []

        processor = processor_cls(submission)
        answers = processor.process()

        # ONLINE은 즉시 answers_ready로
        submission.status = Submission.Status.ANSWERS_READY
        submission.save(update_fields=["status", "updated_at"])

        return answers
