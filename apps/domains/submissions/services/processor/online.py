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
                # ✅ v2 키
                eqid = row.get("exam_question_id")
                # legacy 키
                legacy = row.get("question_id") or row.get("question_number")

                if eqid:
                    yield {
                        "exam_question_id": int(eqid),
                        "question_number": None,
                        "answer": row.get("answer", ""),
                        "meta": row.get("meta") or {"via": "online"},
                    }
                elif legacy:
                    yield {
                        "exam_question_id": None,
                        "question_number": int(legacy),
                        "answer": row.get("answer", ""),
                        "meta": row.get("meta") or {"via": "online_legacy"},
                    }
            return

        # B) dict (key=exam_question_id 권장)
        if isinstance(answers, dict):
            for k, v in answers.items():
                # ✅ 기본은 exam_question_id로 해석
                try:
                    eqid = int(k)
                    yield {
                        "exam_question_id": eqid,
                        "question_number": None,
                        "answer": v if v is not None else "",
                        "meta": {"via": "online"},
                    }
                except Exception:
                    # legacy: key가 number일 수도 있으니 보관만
                    try:
                        qnum = int(k)
                    except Exception:
                        continue
                    yield {
                        "exam_question_id": None,
                        "question_number": qnum,
                        "answer": v if v is not None else "",
                        "meta": {"via": "online_legacy"},
                    }
            return

        return
