# apps/domains/submissions/services/processor/video.py
from __future__ import annotations

from typing import Iterable, Dict, Any

from apps.domains.submissions.services.processor.base import BaseSubmissionProcessor


class VideoSubmissionProcessor(BaseSubmissionProcessor):
    """
    원칙상 영상 분석은 worker 책임.
    API(submissions)에서는:
      - (권장) dispatcher가 AI job 발행
      - (보조) payload/meta에 분석결과가 이미 들어온 경우만 SubmissionAnswer로 정규화 저장
    """

    source = "homework_video"

    def extract_answers(self) -> Iterable[Dict[str, Any]]:
        payload = self.submission.payload or {}
        extracted = payload.get("extracted_answers") or payload.get("answers")

        if isinstance(extracted, list):
            for row in extracted:
                qid = row.get("question_id")
                if not qid:
                    continue
                yield {
                    "question_id": int(qid),
                    "answer": row.get("answer", ""),
                    "meta": row.get("meta") or {"via": "video"},
                }
            return

        if isinstance(extracted, dict):
            for k, v in extracted.items():
                try:
                    qid = int(k)
                except Exception:
                    continue
                yield {
                    "question_id": qid,
                    "answer": v if v is not None else "",
                    "meta": {"via": "video"},
                }
            return

        return
