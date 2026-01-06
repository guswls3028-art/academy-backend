# apps/domains/submissions/services/ai_omr_result_mapper.py
from __future__ import annotations

from typing import Any, Dict, Optional
import logging

from django.db import transaction

from apps.domains.submissions.models import Submission, SubmissionAnswer

logger = logging.getLogger(__name__)


@transaction.atomic
def apply_omr_ai_result(payload: Dict[str, Any]) -> Optional[int]:
    """
    OMR AI 결과를 Submission / SubmissionAnswer에 반영한다.

    ✅ NEXT-2 계약:
    - AI 결과 answers[*].exam_question_id 를 반드시 사용한다.
    - legacy로 question_id가 오면 question_number로 보관만 한다.

    meta 규칙 (고정):
    meta = {
      "omr": {
        "version": "v2",
        "detected": [...],
        "marking": "...",
        "confidence": 0.82,
        "status": "ok"
      }
    }
    """
    submission_id = payload.get("submission_id")
    if not submission_id:
        return None

    try:
        submission = Submission.objects.select_for_update().get(id=int(submission_id))
    except Submission.DoesNotExist:
        logger.warning("[OMR_AI_RESULT] submission %s not found. dropped.", submission_id)
        return None

    # 1) AI 원본 결과는 payload.ai_result 로 보존 (fact)
    base_payload = submission.payload or {}
    base_payload["ai_result"] = payload
    submission.payload = base_payload

    status = payload.get("status")
    error = payload.get("error")

    # 2) FAILED 처리
    if status == "FAILED":
        submission.status = Submission.Status.FAILED
        submission.error_message = error or "AI worker failed"
        submission.save(update_fields=["payload", "status", "error_message", "updated_at"])
        return submission.id

    # 3) 중복 callback 방어
    if submission.status in (
        Submission.Status.ANSWERS_READY,
        Submission.Status.GRADING,
        Submission.Status.DONE,
    ):
        submission.save(update_fields=["payload", "updated_at"])
        return submission.id

    # 4) 답안 매핑
    result = payload.get("result") or {}
    answers = result.get("answers")
    version = str(result.get("version") or "v2")

    if isinstance(answers, list):
        for a in answers:
            # ✅ v2: exam_question_id만 신뢰
            eqid = a.get("exam_question_id")

            # legacy: worker가 아직 전환 안 됐으면 question_id가 올 수 있음
            legacy_qid = a.get("question_id")

            detected = a.get("detected") or []
            marking = a.get("marking") or ""
            confidence = a.get("confidence")
            astatus = a.get("status") or ""

            defaults = {
                "answer": "".join([str(x) for x in detected]),
                "meta": {
                    "omr": {
                        "version": version,
                        "detected": detected,
                        "marking": marking,
                        "confidence": confidence,
                        "status": astatus,
                    }
                },
            }

            # ✅ v2 정상 케이스
            if eqid:
                SubmissionAnswer.objects.update_or_create(
                    submission=submission,
                    exam_question_id=int(eqid),
                    defaults={
                        **defaults,
                        # legacy 보관은 optional
                        "question_number": None,
                    },
                )
                continue

            # ⚠️ legacy 임시: eqid 없으면 number로만 보관
            if legacy_qid:
                SubmissionAnswer.objects.update_or_create(
                    submission=submission,
                    # exam_question_id가 없으므로 NULL row가 생김 → 운영상 허용(전환기)
                    exam_question_id=None,
                    question_number=int(legacy_qid),
                    defaults=defaults,
                )

        submission.status = Submission.Status.ANSWERS_READY
        submission.error_message = ""
        submission.save(update_fields=["payload", "status", "error_message", "updated_at"])
        return submission.id

    # answers 포맷이 없을 경우: payload만 저장
    submission.save(update_fields=["payload", "updated_at"])
    return None
