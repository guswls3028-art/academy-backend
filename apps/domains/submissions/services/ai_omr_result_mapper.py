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

    # 🔐 tenant 교차검증: AI job의 tenant_id와 submission의 tenant_id 일치 확인
    job_id = payload.get("job_id")
    payload_tenant_id = payload.get("tenant_id")
    if payload_tenant_id and hasattr(submission, "tenant_id") and submission.tenant_id:
        if str(payload_tenant_id) != str(submission.tenant_id):
            logger.error(
                "TENANT_ISOLATION_VIOLATION | apply_omr_ai_result | "
                "job_id=%s | payload_tenant=%s | submission_tenant=%s | submission_id=%s",
                job_id, payload_tenant_id, submission.tenant_id, submission_id,
            )
            return None

    # 멱등성 가드: 이미 DISPATCHED 이후 단계로 진행된 submission은 건너뛴다
    if submission.status in _ALREADY_PROCESSED_STATUSES:
        logger.info(
            "apply_omr_ai_result: submission %s already %s, skipping (idempotent)",
            submission_id, submission.status,
        )
        return submission.id

    status = payload.get("status")
    error = payload.get("error")

    # AI worker는 version/answers/identifier를 payload 최상위에 보낸다.
    # 구버전 호환: payload["result"] 하위에 있을 수도 있다.
    _nested = payload.get("result")
    if isinstance(_nested, dict) and _nested:
        result = _nested
    else:
        # payload 최상위에서 AI 결과 필드만 추출
        _exclude = {"submission_id", "status", "error"}
        result = {k: v for k, v in payload.items() if k not in _exclude}

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

    # ── question_number → ExamQuestion.id 매핑 ──
    # AI 엔진은 question_id = question_number(1,2,3...)를 반환한다.
    # SubmissionAnswer.exam_question_id는 ExamQuestion PK여야 하므로 변환 필요.
    qnum_to_pk: dict[int, int] = {}
    if submission.target_type == "exam" and submission.target_id:
        try:
            from apps.domains.exams.models import Sheet, ExamQuestion
            from apps.domains.exams.services.template_resolver import resolve_template_exam
            from apps.domains.exams.models import Exam

            exam = Exam.objects.filter(id=int(submission.target_id)).first()
            if exam:
                template_exam = resolve_template_exam(exam)
                sheet = Sheet.objects.filter(exam=template_exam).first()
                if sheet:
                    for q in ExamQuestion.objects.filter(sheet=sheet).only("id", "number"):
                        qnum_to_pk[int(q.number)] = int(q.id)
        except Exception:
            logger.exception(
                "apply_omr_ai_result: failed to build qnum→pk map for submission %s",
                submission_id,
            )

    manual_required = False
    reasons = []

    # 자동채점 통계: 운영자가 시험별 인식률을 한눈에 볼 수 있도록 aggregate 저장.
    answer_stats: Dict[str, Any] = {
        "total": 0, "ok": 0, "blank": 0, "ambiguous": 0, "error": 0,
        "sum_conf": 0.0, "n_conf": 0,
    }

    for a in answers:
        # v7 engine은 question_id(=question_number), 구버전은 exam_question_id(=PK)
        raw_id = a.get("exam_question_id") or a.get("question_id")
        if not raw_id:
            continue

        # question_number → ExamQuestion PK 변환 (매핑이 있으면 사용)
        eqid = qnum_to_pk.get(int(raw_id), int(raw_id))

        # v10.1: worker가 raw 안에 제공하는 bubble_rects/rect (검토 UI BBox overlay)
        raw_payload = a.get("raw") or {}
        bubble_rects = raw_payload.get("bubble_rects") if isinstance(raw_payload, dict) else None
        question_rect = raw_payload.get("rect") if isinstance(raw_payload, dict) else None

        omr_meta: Dict[str, Any] = {
            "version": a.get("version") or result.get("version"),
            "detected": a.get("detected"),
            "marking": a.get("marking"),
            "confidence": a.get("confidence"),
            "status": a.get("status"),
        }
        if isinstance(bubble_rects, list) and bubble_rects:
            omr_meta["bubble_rects"] = bubble_rects
        if isinstance(question_rect, dict):
            omr_meta["rect"] = question_rect

        SubmissionAnswer.objects.update_or_create(
            submission=submission,
            exam_question_id=int(eqid),
            defaults={
                "tenant": submission.tenant,
                "answer": "".join([str(x) for x in a.get("detected") or []]),
                "meta": {"omr": omr_meta},
            },
        )

        st = str(a.get("status") or "").lower()
        mk = str(a.get("marking") or "").lower()
        conf = a.get("confidence")

        try:
            conf_f = float(conf) if conf is not None else None
        except Exception:
            conf_f = None

        # stats 집계
        answer_stats["total"] += 1
        if st in ("ok", "blank", "ambiguous", "error"):
            answer_stats[st] += 1
        if conf_f is not None:
            answer_stats["sum_conf"] += conf_f
            answer_stats["n_conf"] += 1

        if st != "ok":
            manual_required = True
            reasons.append("ANSWER_STATUS_NOT_OK")

        if mk in ("blank", "multi"):
            manual_required = True
            reasons.append("ANSWER_BLANK_OR_MULTI")

        if conf_f is not None and conf_f < 0.70:
            manual_required = True
            reasons.append("ANSWER_LOW_CONFIDENCE")

    # ── 정렬 실패 명시 ──
    # 워커가 homography/contour/rotation 어느 경로로도 정렬 못 하면 aligned=False.
    # 답안은 대부분 blank로 위장되므로 여기서 명시 사유를 추가해 운영자가 즉시 인지.
    if isinstance(result, dict) and result.get("aligned") is False:
        manual_required = True
        reasons.append("ALIGNMENT_FAILED")

    # 평균 신뢰도 보조 필드
    if answer_stats["n_conf"] > 0:
        answer_stats["avg_confidence"] = round(
            answer_stats["sum_conf"] / answer_stats["n_conf"], 4
        )
    else:
        answer_stats["avg_confidence"] = None
    # 내부 누적값은 저장 생략
    answer_stats.pop("sum_conf", None)
    answer_stats.pop("n_conf", None)

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
    meta["answer_stats"] = answer_stats

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
