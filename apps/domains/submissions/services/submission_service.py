from typing import List, Type
from django.db import transaction

from apps.domains.submissions.models import Submission, SubmissionAnswer
from apps.domains.submissions.services.processor.base import BaseSubmissionProcessor
from apps.domains.submissions.services.processor.online import OnlineSubmissionProcessor
from apps.domains.submissions.services.transition import transit_save

PROCESSOR_MAP: dict[str, Type[BaseSubmissionProcessor]] = {
    Submission.Source.ONLINE: OnlineSubmissionProcessor,
}


class SubmissionService:
    @staticmethod
    @transaction.atomic
    def process(submission: Submission) -> List[SubmissionAnswer]:
        processor_cls = PROCESSOR_MAP.get(submission.source)
        if not processor_cls:
            return []

        processor = processor_cls(submission)
        answers = processor.process()

        transit_save(submission, Submission.Status.ANSWERS_READY, actor="SubmissionService.process")
        return answers
