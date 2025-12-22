# apps/domains/submissions/services/ai_result_mapper.py
from __future__ import annotations

from typing import Any, Dict, Optional

from django.db import transaction

from apps.domains.submissions.models import Submission, SubmissionAnswer


@transaction.atomic
def apply_ai_result(payload: Dict[str, Any]) -> Optional[int]:
    """
    Worker AI 결과를 submissions에 반영.

    지원 스키마:
    1) 답안형(items):
      {
        "submission_id": int,
        "items": [{"question_id":int,"answer":str,"meta":dict?}, ...]
      }
      -> SubmissionAnswer upsert + status=ANSWERS_READY + submission.id 반환

    2) OMR형(v1):
      {
        "submission_id": int,
        "version": "v1",
        "answers": [ {version, question_id, detected, marking, confidence, status, raw?}, ... ]
      }
      -> SubmissionAnswer upsert(meta["omr"]=answer_payload) + status=ANSWERS_READY + submission.id 반환

    3) 분석형(채점 없음):
      {"submission_id": int, "analysis": {...}}
      -> meta["analysis"] 저장 + (HOMEWORK이면) status=DONE + None 반환
    """
    submission_id = payload.get("submission_id")
    if not submission_id:
        return None

    submission = Submission.objects.select_for_update().get(id=int(submission_id))

    # 결과 원본 저장(디버그/추적용)
    base_payload = submission.payload or {}
    base_payload["ai_result"] = payload
    submission.payload = base_payload

    # 1) items 답안형
    items = payload.get("items")
    if isinstance(items, list) and items:
        for item in items:
            qid = item.get("question_id")
            if not qid:
                continue

            SubmissionAnswer.objects.update_or_create(
                submission=submission,
                question_id=int(qid),
                defaults={
                    "answer": str(item.get("answer") or ""),
                    "meta": item.get("meta") or None,
                },
            )

        submission.status = Submission.Status.ANSWERS_READY
        submission.error_message = ""
        meta = submission.meta or {}
        meta["answers_ready"] = True
        meta["answer_count"] = SubmissionAnswer.objects.filter(submission=submission).count()
        submission.meta = meta
        submission.save(update_fields=["payload", "status", "error_message", "meta", "updated_at"])
        return submission.id

    # 2) OMR v1 형식
    answers = payload.get("answers")
    version = payload.get("version")
    if version and isinstance(answers, list) and answers:
        for a in answers:
            qid = (a or {}).get("question_id")
            if not qid:
                continue

            detected = (a or {}).get("detected") or []
            # 사람이 읽기 쉬운 answer 문자열(예: "B" / "BD")
            ans_text = "".join([str(x).strip().upper() for x in detected]) if isinstance(detected, list) else ""

            SubmissionAnswer.objects.update_or_create(
                submission=submission,
                question_id=int(qid),
                defaults={
                    "answer": ans_text,
                    "meta": {"omr": a},  # ✅ results.grader가 meta["omr"]를 읽는다
                },
            )

        submission.status = Submission.Status.ANSWERS_READY
        submission.error_message = ""
        meta = submission.meta or {}
        meta["answers_ready"] = True
        meta["answer_count"] = SubmissionAnswer.objects.filter(submission=submission).count()
        meta["omr_version"] = str(version)
        submission.meta = meta
        submission.save(update_fields=["payload", "status", "error_message", "meta", "updated_at"])
        return submission.id

    # 3) 분석형
    analysis = payload.get("analysis")
    if analysis is not None:
        meta = submission.meta or {}
        meta["analysis"] = analysis
        submission.meta = meta

        if submission.target_type == Submission.TargetType.HOMEWORK:
            submission.status = Submission.Status.DONE

        submission.save(update_fields=["payload", "meta", "status", "updated_at"])
        return None

    submission.save(update_fields=["payload", "updated_at"])
    return None
