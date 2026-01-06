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
        반환 포맷(고정, v2):
            {
              "exam_question_id": int | None,
              "question_number": int | None,   # legacy only
              "answer": str,
              "meta": dict|None
            }
        """
        raise NotImplementedError

    def _save_answers(self, extracted: Iterable[Dict[str, Any]]) -> List[SubmissionAnswer]:
        results: List[SubmissionAnswer] = []

        for item in extracted:
            eqid = item.get("exam_question_id")
            qnum = item.get("question_number")

            # ✅ v2: exam_question_id 우선
            if eqid:
                obj, _ = SubmissionAnswer.objects.update_or_create(
                    submission=self.submission,
                    exam_question_id=int(eqid),
                    defaults={
                        "question_number": None,
                        "answer": str(item.get("answer") or ""),
                        "meta": item.get("meta") or {},
                    },
                )
                results.append(obj)
                continue

            # legacy: number만 있는 경우(전환기)
            if qnum:
                obj = SubmissionAnswer.objects.create(
                    submission=self.submission,
                    exam_question_id=None,
                    question_number=int(qnum),
                    answer=str(item.get("answer") or ""),
                    meta=item.get("meta") or {},
                )
                results.append(obj)

        return results
