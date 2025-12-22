# apps/domains/submissions/services/processor/omr.py
from __future__ import annotations

from typing import Iterable, Dict, Any

from apps.domains.submissions.services.processor.base import BaseSubmissionProcessor


class OMRSubmissionProcessor(BaseSubmissionProcessor):
    """
    원칙상 OMR '추출'은 worker 책임.
    API(submissions)에서는 아래 중 하나만 수행:
      - (권장) dispatcher가 AI job 발행, 결과는 ai callbacks에서 SubmissionAnswer로 저장
      - (보조) 이미 meta/payload에 추출 결과가 들어온 경우 정규화 저장만
    """

    source = "omr_scan"

    def extract_answers(self) -> Iterable[Dict[str, Any]]:
        # 1) worker가 돌려준 결과가 payload/meta에 들어온 케이스만 처리
        payload = self.submission.payload or {}
        extracted = payload.get("extracted_answers") or payload.get("answers")

        # extracted가 online과 같은 스키마면 그대로 처리 가능
        if isinstance(extracted, list):
            for row in extracted:
                qid = row.get("question_id")
                if not qid:
                    continue
                yield {
                    "question_id": int(qid),
                    "answer": row.get("answer", ""),
                    "meta": row.get("meta") or {"via": "omr"},
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
                    "meta": {"via": "omr"},
                }
            return

        return
