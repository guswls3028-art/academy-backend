# apps/domains/submissions/services/processor/online.py
from __future__ import annotations

from typing import Iterable, Dict, Any
from apps.domains.submissions.services.processor.base import BaseSubmissionProcessor


class OnlineSubmissionProcessor(BaseSubmissionProcessor):
    source = "online"

    def extract_answers(self) -> Iterable[Dict[str, Any]]:
        payload = self.submission.payload or {}
        answers = payload.get("answers")

        # A) list
        if isinstance(answers, list):
            for row in answers:
                qid = row.get("question_id")
                if not qid:
                    continue
                yield {
                    "question_id": int(qid),
                    "answer": row.get("answer", ""),
                    "meta": row.get("meta"),
                }
            return

        # B) dict (key=question_id)
        if isinstance(answers, dict):
            for k, v in answers.items():
                try:
                    qid = int(k)
                except Exception:
                    continue
                yield {
                    "question_id": qid,
                    "answer": v if v is not None else "",
                    "meta": {"via": "online"},
                }
            return

        return
