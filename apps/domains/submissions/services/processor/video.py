# apps/domains/submissions/services/processor/video.py
from __future__ import annotations

from typing import Iterable, Dict, Any

from apps.domains.submissions.services.processor.base import BaseSubmissionProcessor

class VideoSubmissionProcessor(BaseSubmissionProcessor):
    source = "homework_video"

    def extract_answers(self):
        payload = self.submission.payload or {}
        extracted = payload.get("extracted_answers") or payload.get("answers")

        if not isinstance(extracted, (list, dict)):
            return

        rows = extracted if isinstance(extracted, list) else extracted.items()

        for row in rows:
            # worker가 v2 계약을 지킨 경우만 처리
            eqid = (
                row.get("exam_question_id")
                if isinstance(row, dict)
                else None
            )

            if not eqid:
                # ❌ legacy video 답안은 정규 저장하지 않음
                continue

            yield {
                "exam_question_id": int(eqid),
                "question_number": None,
                "answer": row.get("answer", ""),
                "meta": row.get("meta") or {"via": "video"},
            }
