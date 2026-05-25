from __future__ import annotations

from typing import Any, Dict, Optional
import logging
from datetime import datetime, timezone

from django.db import transaction
from django.db.models import Q

from apps.domains.submissions.models import Submission, SubmissionAnswer
from apps.domains.submissions.services.omr_submission_guards import (
    duplicate_conflict_payload,
    find_conflicting_exam_submission,
    lock_exam_enrollment_candidate,
)
from apps.domains.submissions.services.transition import transit
from apps.domains.results.services.answer_matching import answer_matches, correct_answer_sets

logger = logging.getLogger(__name__)

# 멱등성: 이미 처리된 submission은 callback을 무시한다
_ALREADY_PROCESSED_STATUSES = frozenset({
    Submission.Status.ANSWERS_READY,
    Submission.Status.GRADING,
    Submission.Status.DONE,
    Submission.Status.SUPERSEDED,
})


def _hamming(a: str, b: str) -> int:
    """동일 길이 문자열 간 Hamming 거리. 길이 다르면 큰 값."""
    if len(a) != len(b):
        return max(len(a), len(b))
    return sum(1 for x, y in zip(a, b) if x != y)


def _clean_tail8(value: str) -> str:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    return digits[-8:] if len(digits) >= 8 else digits


def _tail8_variants(value: str) -> set[str]:
    """
    전화번호/식별번호 비교용 tail 후보.

    운영 값은 숫자 8자리지만, 오래된 테스트/fixture에는 S001 같은
    알파벳이 섞인 pseudo-phone이 있다. 숫자 정규화와 legacy compact tail을
    모두 비교해 기존 보안 회귀를 유지한다.
    """
    raw = str(value or "").strip()
    variants: set[str] = set()

    digit_tail = _clean_tail8(raw)
    if digit_tail:
        variants.add(digit_tail)

    compact = "".join(ch for ch in raw if ch.isalnum()).upper()
    if len(compact) >= 8:
        variants.add(compact[-8:])
    elif compact:
        variants.add(compact)

    return variants


def _exact_enrollment_ids_by_phone(
    *,
    exam_id: int,
    phone_last8: str,
    tenant,
) -> set[int]:
    """시험 대상자 중 전화번호 뒤 8자리 exact match enrollment id 집합."""
    from apps.domains.enrollment.models import Enrollment

    tails = {tail for tail in _tail8_variants(phone_last8) if len(tail) == 8}
    if not tails:
        return set()

    out: set[int] = set()
    enrollments = Enrollment.objects.filter(
        Q(exam_enrollments__exam_id=exam_id)
        | Q(session_enrollments__session__exams__id=exam_id),
        tenant=tenant,
        status="ACTIVE",
        student__deleted_at__isnull=True,
    ).select_related("student").distinct()
    for enr in enrollments:
        student = getattr(enr, "student", None)
        if not student:
            continue
        student_tails = _tail8_variants(getattr(student, "phone", "") or "")
        parent_tails = _tail8_variants(getattr(student, "parent_phone", "") or "")
        omr_tails = _tail8_variants(getattr(student, "omr_code", "") or "")
        if tails & (student_tails | parent_tails | omr_tails):
            out.add(int(enr.id))
    return out


def _ambiguous_identifier_has_competing_exact_match(
    *,
    identifier: dict[str, Any],
    accepted_code: str,
    accepted_enrollment_id: int,
    exam_id: int,
    tenant,
) -> bool:
    """
    식별번호 한 자리 ambiguous라도, 가능한 대체 코드가 다른 시험 대상자를
    정확히 가리키지 않으면 자동 매칭해도 안전하다.
    """
    base = _clean_tail8(accepted_code)
    if len(base) != 8:
        return True

    digits = identifier.get("digits") if isinstance(identifier, dict) else None
    if not isinstance(digits, list):
        return True

    candidate_codes: set[str] = set()
    for digit in digits:
        if not isinstance(digit, dict):
            continue
        if str(digit.get("status") or "").lower() != "ambiguous":
            continue
        try:
            raw_idx = int(digit.get("digit_index"))
        except Exception:
            continue
        idx = raw_idx if 0 <= raw_idx < len(base) else raw_idx - 1
        if idx < 0 or idx >= len(base):
            continue

        marks = digit.get("marks")
        if not isinstance(marks, list):
            continue
        for mark in marks[1:4]:
            if not isinstance(mark, dict):
                continue
            raw_number = mark.get("number")
            if raw_number is None:
                continue
            alt = str(raw_number)
            if len(alt) != 1 or not alt.isdigit() or alt == base[idx]:
                continue
            candidate_codes.add(f"{base[:idx]}{alt}{base[idx + 1:]}")

    for code in candidate_codes:
        matches = _exact_enrollment_ids_by_phone(
            exam_id=exam_id,
            phone_last8=code,
            tenant=tenant,
        )
        if matches and (matches != {int(accepted_enrollment_id)}):
            return True
    return False


def _ambiguous_answer_can_change_score(
    *,
    detected_values: list[str],
    correct_answer: Any,
) -> bool:
    """
    애매한 마킹이 실제 점수를 바꿀 가능성이 있을 때만 검토로 보낸다.
    정답 후보와 전혀 겹치지 않는 애매한 선/낙서는 자동 오답으로 확정 가능하다.
    """
    detected = frozenset(str(v).strip() for v in detected_values if str(v).strip())
    if not detected:
        return False

    correct_sets = correct_answer_sets(correct_answer)
    if not correct_sets:
        return True
    if detected in correct_sets:
        return False

    return any(bool(detected & correct) for correct in correct_sets)


def _resolve_enrollment_by_phone(
    *,
    exam_id: int,
    phone_last8: str,
    tenant,
) -> tuple[int | None, str]:
    """
    전화번호 뒤 8자리로 해당 시험의 enrollment을 찾는다.

    매칭 순서:
    1. 정확 매칭 (학생 본인 휴대폰 → 학부모 휴대폰)
    2. 정확 매칭 0건일 때만 fuzzy fallback: Hamming 거리 ≤1 후보 검색.
       후보가 정확히 1명일 때만 자동 매칭, 그 외(0/2+)는 None → 수동 식별.

    Returns:
        (enrollment_id | None, match_kind)
          - "exact" : 정확 매칭 1건
          - "fuzzy" : Hamming≤1 fallback 1건 (운영자 확인 권장)
          - "none"  : 매칭 실패 (0건 또는 다수)
    """
    from apps.domains.enrollment.models import Enrollment

    # 시험에 연결된 enrollment 중에서 검색
    enrollments = Enrollment.objects.filter(
        Q(exam_enrollments__exam_id=exam_id)
        | Q(session_enrollments__session__exams__id=exam_id),
        tenant=tenant,
        status="ACTIVE",
        student__deleted_at__isnull=True,
    ).select_related("student").distinct()

    lookup_tails = {tail for tail in _tail8_variants(phone_last8) if len(tail) == 8}
    lookup_digit_tails = {tail for tail in lookup_tails if tail.isdigit()}

    exact_matches: list[int] = []
    fuzzy_candidates: list[tuple[int, int]] = []  # (enrollment_id, hamming_distance)

    for enr in enrollments:
        student = getattr(enr, "student", None)
        if not student:
            continue
        s_tails = _tail8_variants(getattr(student, "phone", "") or "")
        p_tails = _tail8_variants(getattr(student, "parent_phone", "") or "")
        o_tails = _tail8_variants(getattr(student, "omr_code", "") or "")

        if lookup_tails & (s_tails | p_tails | o_tails):
            exact_matches.append(enr.id)
            continue

        # fuzzy 후보: 길이 8이고 Hamming 거리 ≤1
        student_digit_tails = {tail for tail in s_tails if len(tail) == 8 and tail.isdigit()}
        parent_digit_tails = {tail for tail in p_tails if len(tail) == 8 and tail.isdigit()}
        omr_digit_tails = {tail for tail in o_tails if len(tail) == 8 and tail.isdigit()}
        for candidate_tail in student_digit_tails | parent_digit_tails | omr_digit_tails:
            for lookup_tail in lookup_digit_tails:
                d = _hamming(candidate_tail, lookup_tail)
                if d <= 1:
                    fuzzy_candidates.append((enr.id, d))
                    break
            else:
                continue
            break

    # 1) 정확 매칭이 정확히 1건이면 채택
    if len(exact_matches) == 1:
        return exact_matches[0], "exact"
    if len(exact_matches) >= 2:
        return None, "none"  # 정확 매칭 다수 → 수동

    # 2) 정확 매칭 0건일 때만 fuzzy fallback (1자리 OMR 인식 오류 흡수)
    fuzzy_unique = {eid for eid, _ in fuzzy_candidates}
    if len(fuzzy_unique) == 1:
        eid = next(iter(fuzzy_unique))
        logger.info(
            "ai_omr_result_mapper: fuzzy phone match (hamming<=1) accepted | exam=%s | enr=%s",
            exam_id, eid,
        )
        return eid, "fuzzy"

    # 0 또는 2+ 매칭 → 수동 식별 필요
    return None, "none"


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
    correct_answers_by_pk: dict[str, Any] = {}
    qnum_map_built = False
    if submission.target_type == "exam" and submission.target_id:
        try:
            from apps.domains.exams.models import AnswerKey, Sheet, ExamQuestion
            from apps.domains.exams.services.template_resolver import resolve_template_exam
            from apps.domains.exams.models import Exam

            exam = Exam.objects.filter(id=int(submission.target_id)).first()
            if exam:
                template_exam = resolve_template_exam(exam)
                sheet = Sheet.objects.filter(exam=template_exam).first()
                if sheet:
                    for q in ExamQuestion.objects.filter(sheet=sheet).only("id", "number"):
                        qnum_to_pk[int(q.number)] = int(q.id)
                    qnum_map_built = True
                answer_key = AnswerKey.objects.filter(exam=template_exam).first()
                if answer_key and isinstance(answer_key.answers, dict):
                    correct_answers_by_pk = answer_key.answers
        except Exception:
            logger.exception(
                "apply_omr_ai_result: failed to build qnum→pk map for submission %s",
                submission_id,
            )

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
            and answer_matches(detected_values, correct_answers_by_pk.get(str(eqid)))
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

        score_ambiguous = _ambiguous_answer_can_change_score(
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

    # ✅ 식별자 → enrollment 매칭 (전화번호 뒤 8자리 → 학생 자동 식별)
    # status가 ok가 아니어도 raw_identifier에 8자리 모두 결정됐으면 best-effort 매칭 시도.
    # 한 자리만 ambiguous인 경우에도 정확/fuzzy 매칭이 단일 학생을 가리키면 자동 식별 가능.
    enrollment_id = None
    identifier_status = "missing"
    identifier_match_kind = "none"
    ident_status = ""
    detected_code = ""

    if isinstance(identifier, dict):
        ident_status = str(identifier.get("status") or "").lower()
        # raw_identifier에 ?(blank) 없는 경우만 시도 (ambiguous는 통과, blank/error는 차단)
        detected_code = str(
            identifier.get("identifier")
            or identifier.get("raw_identifier")
            or ""
        ).strip()
        ident_complete = (
            len(detected_code) == 8
            and "?" not in detected_code
            and ident_status in ("ok", "ambiguous")
        )
        if ident_status in ("ok", "ambiguous"):
            identifier_status = "detected"

        if ident_complete and submission.target_id:
            # 전화번호 뒤 8자리로 enrollment 조회 (정확 매칭 → fuzzy fallback)
            enrollment_id, identifier_match_kind = _resolve_enrollment_by_phone(
                exam_id=int(submission.target_id),
                phone_last8=detected_code,
                tenant=submission.tenant,
            )
            ambiguous_ident = ident_status == "ambiguous"
            if enrollment_id and identifier_match_kind == "exact":
                if ambiguous_ident:
                    has_competitor = _ambiguous_identifier_has_competing_exact_match(
                        identifier=identifier,
                        accepted_code=detected_code,
                        accepted_enrollment_id=int(enrollment_id),
                        exam_id=int(submission.target_id),
                        tenant=submission.tenant,
                    )
                    if has_competitor:
                        # 대체 digit 후보가 다른 시험 대상자를 가리킬 수 있으면 운영자 확인.
                        identifier_status = "matched_ambiguous"
                        manual_required = True
                        reasons.append("IDENTIFIER_AMBIGUOUS_DIGIT")
                    else:
                        # 애매한 digit 후보가 실제 경쟁 대상자를 만들지 않으면 자동 확정.
                        identifier_status = "matched_ambiguous_resolved"
                else:
                    identifier_status = "matched"
            elif enrollment_id and identifier_match_kind == "fuzzy":
                # 자동 매칭은 하되 운영자 확인을 권장 (1자리 OMR 인식 오류 흡수)
                identifier_status = "matched_fuzzy"
                manual_required = True
                reasons.append("IDENTIFIER_FUZZY_MATCH")
                if ambiguous_ident:
                    reasons.append("IDENTIFIER_AMBIGUOUS_DIGIT")
            else:
                identifier_status = "no_match"
                reasons.append("IDENTIFIER_NO_ENROLLMENT_MATCH")

    if not enrollment_id:
        if not isinstance(identifier, dict) or not detected_code:
            reasons.append("IDENTIFIER_MISSING")
        elif (
            "?" in detected_code
            or len(_clean_tail8(detected_code)) != 8
            or ident_status not in ("ok", "ambiguous")
        ):
            identifier_status = "incomplete"
            reasons.append("IDENTIFIER_INCOMPLETE")

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
