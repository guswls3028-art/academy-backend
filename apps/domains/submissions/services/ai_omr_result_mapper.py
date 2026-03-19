from __future__ import annotations

from typing import Any, Dict, Optional
import logging
from datetime import datetime, timezone

from django.db import transaction

from apps.domains.submissions.models import Submission, SubmissionAnswer
from apps.domains.submissions.services.transition import transit, InvalidTransitionError

logger = logging.getLogger(__name__)

# 멱등성: 이미 처리된 submission은 callback을 무시한다
_ALREADY_PROCESSED_STATUSES = frozenset({
    Submission.Status.ANSWERS_READY,
    Submission.Status.GRADING,
    Submission.Status.DONE,
    Submission.Status.SUPERSEDED,
})


def _resolve_enrollment_by_phone(
    *,
    exam_id: int,
    phone_last8: str,
    tenant,
) -> int | None:
    """
    전화번호 뒤 8자리로 해당 시험의 enrollment을 찾는다.

    매칭 순서:
    1. 학생 본인 휴대폰 뒤 8자리
    2. 학부모 휴대폰 뒤 8자리

    중복 시 None 반환 (수동 매칭 필요).
    """
    from apps.domains.enrollment.models import Enrollment

    # 시험에 연결된 enrollment 중에서 검색
    enrollments = Enrollment.objects.filter(
        exam_enrollments__exam_id=exam_id,
        tenant=tenant,
    ).select_related("student").distinct()

    matches = []
    for enr in enrollments:
        student = getattr(enr, "student", None)
        if not student:
            continue
        s_phone = str(getattr(student, "phone", "") or "").replace("-", "").strip()
        p_phone = str(getattr(student, "parent_phone", "") or "").replace("-", "").strip()

        if s_phone and s_phone[-8:] == phone_last8:
            matches.append(enr.id)
        elif p_phone and p_phone[-8:] == phone_last8:
            matches.append(enr.id)

    if len(matches) == 1:
        return matches[0]

    # 0 또는 2+ 매칭 → 수동 식별 필요
    return None


@transaction.atomic
def apply_omr_ai_result(payload: Dict[str, Any]) -> Optional[int]:
    """
    OMR AI 결과를 Submission에 반영. 순서: identifier 매칭 → NEEDS_IDENTIFICATION 결정 → 답안 저장.
    AI job 최종 상태(DONE/REVIEW_REQUIRED/FAILED)는 InternalAIJobResultView + status_resolver에서 결정.
    """
    submission_id = payload.get("submission_id")
    if not submission_id:
        return None

    try:
        submission = Submission.objects.select_for_update().get(id=int(submission_id))
    except Submission.DoesNotExist:
        logger.warning("apply_omr_ai_result: submission %s not found", submission_id)
        return None

    # 멱등성 가드: 이미 DISPATCHED 이후 단계로 진행된 submission은 건너뛴다
    if submission.status in _ALREADY_PROCESSED_STATUSES:
        logger.info(
            "apply_omr_ai_result: submission %s already %s, skipping (idempotent)",
            submission_id, submission.status,
        )
        return submission.id

    status = payload.get("status")
    result = payload.get("result") or {}
    error = payload.get("error")

    meta = dict(submission.meta or {})
    meta["ai_result"] = {
        "status": status,
        "result": result,
        "error": error,
        "received_at": datetime.now(timezone.utc).isoformat(),
        "kind": "omr_scan",
    }
    submission.meta = meta

    if status == "FAILED":
        transit(submission, Submission.Status.FAILED, error_message=error or "AI worker failed", actor="ai_omr_mapper")
        submission.save(update_fields=["meta", "status", "error_message", "updated_at"])
        return submission.id

    answers = result.get("answers") or []
    identifier = result.get("identifier")

    manual_required = False
    reasons = []

    for a in answers:
        # v7 engine은 question_id, 구버전은 exam_question_id
        eqid = a.get("exam_question_id") or a.get("question_id")
        if not eqid:
            continue

        SubmissionAnswer.objects.update_or_create(
            submission=submission,
            exam_question_id=int(eqid),
            defaults={
                "answer": "".join([str(x) for x in a.get("detected") or []]),
                "meta": {
                    "omr": {
                        "version": result.get("version"),
                        "detected": a.get("detected"),
                        "marking": a.get("marking"),
                        "confidence": a.get("confidence"),
                        "status": a.get("status"),
                    }
                },
            },
        )

        st = str(a.get("status") or "").lower()
        mk = str(a.get("marking") or "").lower()
        conf = a.get("confidence")

        try:
            conf_f = float(conf) if conf is not None else None
        except Exception:
            conf_f = None

        if st != "ok":
            manual_required = True
            reasons.append("ANSWER_STATUS_NOT_OK")

        if mk in ("blank", "multi"):
            manual_required = True
            reasons.append("ANSWER_BLANK_OR_MULTI")

        if conf_f is not None and conf_f < 0.70:
            manual_required = True
            reasons.append("ANSWER_LOW_CONFIDENCE")

    # ✅ 식별자 → enrollment 매칭 (전화번호 뒤 8자리 → 학생 자동 식별)
    enrollment_id = None
    identifier_status = "missing"

    if isinstance(identifier, dict) and identifier.get("status") == "ok":
        detected_code = str(identifier.get("identifier") or "").strip()
        identifier_status = "detected"

        if detected_code and len(detected_code) == 8 and submission.target_id:
            # 전화번호 뒤 8자리로 enrollment 조회
            enrollment_id = _resolve_enrollment_by_phone(
                exam_id=int(submission.target_id),
                phone_last8=detected_code,
                tenant=submission.tenant,
            )
            if enrollment_id:
                identifier_status = "matched"
            else:
                identifier_status = "no_match"
                reasons.append("IDENTIFIER_NO_ENROLLMENT_MATCH")

    identifier_ok = enrollment_id is not None

    meta.setdefault("manual_review", {})
    meta["manual_review"]["required"] = bool(manual_required or not identifier_ok)
    meta["manual_review"]["reasons"] = sorted(set(reasons))
    meta["manual_review"]["updated_at"] = datetime.now(timezone.utc).isoformat()
    meta["identifier_status"] = identifier_status

    submission.meta = meta

    if not identifier_ok:
        transit(submission, Submission.Status.NEEDS_IDENTIFICATION, actor="ai_omr_mapper")
        submission.save(update_fields=["meta", "status", "error_message", "updated_at"])
        return submission.id

    submission.enrollment_id = int(enrollment_id)
    transit(submission, Submission.Status.ANSWERS_READY, actor="ai_omr_mapper")
    submission.save(update_fields=["meta", "status", "enrollment_id", "error_message", "updated_at"])

    return submission.id


# 콜백에서 사용 (apply_omr_ai_result와 동일, 네이밍 일관용)
apply_ai_result = apply_omr_ai_result
