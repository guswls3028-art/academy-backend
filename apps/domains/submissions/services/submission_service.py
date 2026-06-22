from typing import List, Type
from django.db import transaction

from apps.domains.submissions.models import Submission, SubmissionAnswer
from apps.domains.submissions.services.processor.base import BaseSubmissionProcessor
from apps.domains.submissions.services.processor.online import OnlineSubmissionProcessor
from apps.domains.submissions.services.lifecycle import mark_answers_ready

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

        mark_answers_ready(submission, actor="SubmissionService.process")
        return answers
