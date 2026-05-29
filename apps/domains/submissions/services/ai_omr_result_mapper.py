from __future__ import annotations

from typing import Any, Dict, Optional
import logging
from datetime import datetime, timezone

from django.db import transaction

from apps.domains.submissions.models import Submission, SubmissionAnswer
from apps.domains.submissions.services.omr_submission_guards import (
    duplicate_conflict_payload,
    find_conflicting_exam_submission,
)
from apps.domains.submissions.services.transition import transit
from apps.support.omr.answer_policy import (
    ambiguous_answer_can_change_score,
    answer_matches_expected,
)
from apps.support.omr.candidate_matching import (
    clean_tail8,
    lock_exam_enrollment_candidate,
)
from apps.support.omr.exam_structure import load_submission_exam_structure

logger = logging.getLogger(__name__)

# 멱등성: 이미 처리된 submission은 callback을 무시한다
_ALREADY_PROCESSED_STATUSES = frozenset({
    Submission.Status.ANSWERS_READY,
    Submission.Status.GRADING,
    Submission.Status.DONE,
    Submission.Status.SUPERSEDED,
})


def _validate_worker_contract(
    submission: Submission, payload: Dict[str, Any]
) -> Optional[int]:
    """
    AI worker callback payload 를 OMRWorkerCallback schema 로 검증.

    Returns:
        None  schema 통과 → caller 는 정상 흐름 계속.
        submission.id  schema 위반 → manual_review 마킹 + meta 기록 후 caller 종료.

    silent failure 차단: 새 worker version, 필수 키 누락, 타입 오류가 prod 에 들어와도
    학원장 화면에 "검토 필요" 표시가 뜨고 audit log 가 남는다.
    """
    from apps.domains.submissions.omr_pipeline.contracts import parse_worker_callback

    callback, err = parse_worker_callback(payload)
    if callback is not None:
        return None

    logger.error(
        "OMR_WORKER_CONTRACT_VIOLATION | submission_id=%s | error=%s",
        submission.id, err,
    )
    now_iso = datetime.now(timezone.utc).isoformat()
    meta = dict(submission.meta or {})
    meta.setdefault("manual_review", {})
    meta["manual_review"]["required"] = True
    reasons = list(meta["manual_review"].get("reasons") or [])
    if "WORKER_CONTRACT_VIOLATION" not in reasons:
        reasons.append("WORKER_CONTRACT_VIOLATION")
    meta["manual_review"]["reasons"] = sorted(set(reasons))
    meta["manual_review"]["updated_at"] = now_iso
    meta["worker_contract_violation"] = {
        "at": now_iso,
        "error": (err or "")[:2000],
    }
    submission.meta = meta
    submission.save(update_fields=["meta", "updated_at"])
    return submission.id


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

    # ── worker contract 검증 (silent failure 차단) ──────────────────────────
    # 새 worker version / 깨진 payload / 빠진 필수 키 들어오면 즉시 manual_review
    # 강제하고 자동 채점은 막는다. 학원장이 빈 시트나 잘못된 점수를 받는 것보다
    # "검토 필요" 표시가 안전하다.
    _violation_id = _validate_worker_contract(submission, payload)
    if _violation_id is not None:
        return _violation_id

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
    exam_structure = load_submission_exam_structure(submission)
    qnum_to_pk = exam_structure.qnum_to_pk
    correct_answers_by_pk = exam_structure.correct_answers_by_pk
    qnum_map_built = exam_structure.qnum_map_built

    manual_required = False
    reasons = []
    unmapped_questions: list[int] = []

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

        # question_number → ExamQuestion PK 변환.
        # qnum 매핑이 구축되었지만 해당 번호가 매핑에 없으면 다른 시험 PK 충돌 위험 → skip.
        # 매핑 자체가 미구축(qnum_map_built=False)인 구버전 데이터에서만 raw_id를 PK로 fallback.
        raw_id_int = int(raw_id)
        if qnum_map_built:
            eqid_opt = qnum_to_pk.get(raw_id_int)
            if eqid_opt is None:
                unmapped_questions.append(raw_id_int)
                logger.warning(
                    "apply_omr_ai_result: question %s not in sheet | submission=%s | exam=%s",
                    raw_id_int, submission_id, submission.target_id,
                )
                continue
            eqid = eqid_opt
        else:
            # 매핑 미구축: 구버전 호환 fallback
            eqid = raw_id_int

        detected_values = [str(x).strip() for x in (a.get("detected") or []) if str(x).strip()]
        detected_answer = ",".join(detected_values)
        expected_multi_ok = (
            len(detected_values) > 1
            and answer_matches_expected(detected_values, correct_answers_by_pk.get(str(eqid)))
        )

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
        if expected_multi_ok:
            omr_meta["expected_multi_answer"] = True
        if isinstance(bubble_rects, list) and bubble_rects:
            omr_meta["bubble_rects"] = bubble_rects
        if isinstance(question_rect, dict):
            omr_meta["rect"] = question_rect

        SubmissionAnswer.objects.update_or_create(
            submission=submission,
            exam_question_id=int(eqid),
            defaults={
                "tenant": submission.tenant,
                "answer": detected_answer,
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

        score_ambiguous = ambiguous_answer_can_change_score(
            detected_values=detected_values,
            correct_answer=correct_answers_by_pk.get(str(eqid)),
        )

        if st == "error":
            manual_required = True
            reasons.append("ANSWER_STATUS_NOT_OK")
        elif st not in ("ok", "blank") and not expected_multi_ok and score_ambiguous:
            manual_required = True
            reasons.append("ANSWER_SCORE_AMBIGUOUS")

        if st == "low_confidence" and not expected_multi_ok and score_ambiguous:
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

    # ✅ 식별자 → enrollment 매칭 (IdentifierMatcher 단일 진입점)
    # 시험 내 학생 phone tail / parent_phone tail / omr_code 중 어느 쪽이든 매칭 +
    # 1 자리 변형이 다른 학생을 가리키면 needs_review (워커 status 가 'ok' 든
    # 'ambiguous' 든 동일). silent 1-digit error 방어.
    from apps.domains.submissions.omr_pipeline.services.identifier_matcher import (
        IdentifierMatcher,
    )

    if submission.target_id and isinstance(identifier, dict):
        matcher = IdentifierMatcher(tenant=submission.tenant, exam_id=int(submission.target_id))
        match_result = matcher.match(identifier)
    else:
        from apps.domains.submissions.omr_pipeline.services.identifier_matcher import (
            IdentifierMatchResult,
        )
        match_result = IdentifierMatchResult(None, "missing", False, ["IDENTIFIER_MISSING"])

    enrollment_id = match_result.enrollment_id
    identifier_status = match_result.identifier_status
    identifier_match_kind = "fuzzy" if match_result.kind == "fuzzy" else (
        "exact" if match_result.kind in ("exact", "exact_with_competitor") else "none"
    )
    if match_result.needs_review:
        manual_required = True
    for r in match_result.review_reasons:
        if r != "IDENTIFIER_AMBIGUOUS_DIGIT_RESOLVED":
            reasons.append(r)

    # 식별 진단 메타 (UI 가 cluster / review 표시할 때 사용)
    detected_code = ""
    if isinstance(identifier, dict):
        detected_code = str(
            identifier.get("identifier") or identifier.get("raw_identifier") or ""
        ).strip()
    ident_status = (
        str(identifier.get("status") or "").lower() if isinstance(identifier, dict) else ""
    )

    identifier_ok = enrollment_id is not None
    duplicate_conflict = None
    if identifier_ok and submission.target_id:
        exam_enrollment_locked = lock_exam_enrollment_candidate(
            tenant=submission.tenant,
            exam_id=int(submission.target_id),
            enrollment_id=int(enrollment_id),
        )
        if not exam_enrollment_locked:
            identifier_ok = False
            enrollment_id = None
            identifier_status = "no_match"
            reasons.append("IDENTIFIER_NO_EXAM_ENROLLMENT")
        else:
            duplicate_conflict = find_conflicting_exam_submission(
                tenant=submission.tenant,
                exam_id=int(submission.target_id),
                enrollment_id=int(enrollment_id),
                exclude_submission_id=int(submission.id),
            )
            if duplicate_conflict:
                manual_required = True
                identifier_status = "matched_duplicate"
                reasons.append("DUPLICATE_ENROLLMENT")

    if unmapped_questions:
        manual_required = True
        reasons.append("ANSWER_QNUM_NOT_IN_SHEET")

    meta.setdefault("manual_review", {})
    meta["manual_review"]["required"] = bool(manual_required or not identifier_ok)
    meta["manual_review"]["reasons"] = sorted(set(reasons))
    meta["manual_review"]["updated_at"] = datetime.now(timezone.utc).isoformat()
    if unmapped_questions:
        meta["manual_review"]["unmapped_questions"] = sorted(set(unmapped_questions))
    meta["identifier_status"] = identifier_status
    meta["identifier_match_kind"] = identifier_match_kind
    meta["answer_stats"] = answer_stats
    if duplicate_conflict:
        meta["duplicate_conflict"] = duplicate_conflict_payload(duplicate_conflict)

    submission.meta = meta

    if not identifier_ok:
        transit(submission, Submission.Status.NEEDS_IDENTIFICATION, actor="ai_omr_mapper")
        submission.save(update_fields=["meta", "status", "error_message", "updated_at"])
        return submission.id

    if duplicate_conflict:
        transit(
            submission,
            Submission.Status.NEEDS_IDENTIFICATION,
            error_message="duplicate_enrollment",
            actor="ai_omr_mapper",
        )
        submission.save(update_fields=["meta", "status", "error_message", "updated_at"])
        return submission.id

    submission.enrollment_id = int(enrollment_id)
    transit(submission, Submission.Status.ANSWERS_READY, actor="ai_omr_mapper")
    submission.save(update_fields=["meta", "status", "enrollment_id", "error_message", "updated_at"])

    return submission.id


# 콜백에서 사용 (apply_omr_ai_result와 동일, 네이밍 일관용)
apply_ai_result = apply_omr_ai_result
