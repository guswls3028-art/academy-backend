# apps/domains/submissions/services/processor/base.py
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterable, Dict, Any, List

from django.db import transaction
from apps.domains.submissions.models import Submission, SubmissionAnswer


class BaseSubmissionProcessor(ABC):
    """
    submissions 내 processor = '답안 중간산물 저장'까지만
    - 채점/정답비교/점수계산 절대 금지
    """
    source: str = "base"

    def __init__(self, submission: Submission):
        self.submission = submission

    @transaction.atomic
    def process(self) -> List[SubmissionAnswer]:
        extracted = list(self.extract_answers())
        return self._save_answers(extracted)

    @abstractmethod
    def extract_answers(self) -> Iterable[Dict[str, Any]]:
        """
        반환 포맷(고정):
            {"question_id": int, "answer": str, "meta": dict|None}
        """
        raise NotImplementedError

    def _save_answers(self, extracted: Iterable[Dict[str, Any]]) -> List[SubmissionAnswer]:
        results: List[SubmissionAnswer] = []

        for item in extracted:
            qid = item.get("question_id")
            if not qid:
                continue

            obj, _ = SubmissionAnswer.objects.update_or_create(
                submission=self.submission,
                question_id=int(qid),
                defaults={
                    "answer": str(item.get("answer") or ""),
                    "meta": item.get("meta") or None,
                },
            )
            results.append(obj)

        return results
