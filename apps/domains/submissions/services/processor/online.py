# apps/domains/submissions/services/processor/online.py
from __future__ import annotations

from typing import Iterable, Dict, Any
from apps.domains.submissions.services.processor.base import BaseSubmissionProcessor


class OnlineSubmissionProcessor(BaseSubmissionProcessor):
    source = "online"

    def extract_answers(self) -> Iterable[Dict[str, Any]]:
        payload = self.submission.payload or {}
        answers = payload.get("answers")

        if not isinstance(answers, (list, dict)):
            return

        rows = answers if isinstance(answers, list) else answers.items()

        for row in rows:
            eqid = (
                row.get("exam_question_id")
                if isinstance(row, dict)
                else None
            )
            if not eqid:
                continue

            yield {
                "exam_question_id": int(eqid),
                "answer": row.get("answer", ""),
                "meta": row.get("meta") or {"via": "online"},
            }
