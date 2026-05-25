from __future__ import annotations

import logging

from django.db.models import Q

logger = logging.getLogger(__name__)


def _hamming(a: str, b: str) -> int:
    """동일 길이 문자열 간 Hamming 거리. 길이 다르면 큰 값."""
    if len(a) != len(b):
        return max(len(a), len(b))
    return sum(1 for x, y in zip(a, b) if x != y)


def clean_tail8(value: str) -> str:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    return digits[-8:] if len(digits) >= 8 else digits


def tail8_variants(value: str) -> set[str]:
    """
    전화번호/식별번호 비교용 tail 후보.

    운영 값은 숫자 8자리지만, 오래된 테스트/fixture에는 S001 같은
    알파벳이 섞인 pseudo-phone이 있다. 숫자 정규화와 legacy compact tail을
    모두 비교해 기존 보안 회귀를 유지한다.
    """
    raw = str(value or "").strip()
    variants: set[str] = set()

    digit_tail = clean_tail8(raw)
    if digit_tail:
        variants.add(digit_tail)

    compact = "".join(ch for ch in raw if ch.isalnum()).upper()
    if len(compact) >= 8:
        variants.add(compact[-8:])
    elif compact:
        variants.add(compact)

    return variants


def _candidate_enrollments(*, exam_id: int, tenant):
    from apps.domains.enrollment.models import Enrollment

    return (
        Enrollment.objects.filter(
            Q(
                exam_enrollments__exam_id=exam_id,
                exam_enrollments__exam__tenant=tenant,
            )
            | Q(
                session_enrollments__session__exams__id=exam_id,
                session_enrollments__session__exams__tenant=tenant,
            ),
            tenant=tenant,
            status="ACTIVE",
            student__deleted_at__isnull=True,
        )
        .select_related("student")
        .distinct()
    )


def exact_enrollment_ids_by_identifier(
    *,
    exam_id: int,
    identifier: str,
    tenant,
) -> set[int]:
    """시험 대상자 중 식별번호/전화번호 뒤 8자리 exact match enrollment id 집합."""
    tails = {tail for tail in tail8_variants(identifier) if len(tail) == 8}
    if not tails:
        return set()

    out: set[int] = set()
    for enr in _candidate_enrollments(exam_id=exam_id, tenant=tenant):
        student = getattr(enr, "student", None)
        if not student:
            continue
        student_tails = tail8_variants(getattr(student, "phone", "") or "")
        parent_tails = tail8_variants(getattr(student, "parent_phone", "") or "")
        omr_tails = tail8_variants(getattr(student, "omr_code", "") or "")
        if tails & (student_tails | parent_tails | omr_tails):
            out.add(int(enr.id))
    return out


def resolve_enrollment_by_identifier(
    *,
    exam_id: int,
    identifier: str,
    tenant,
) -> tuple[int | None, str]:
    """
    OMR 식별번호로 해당 시험의 enrollment를 찾는다.

    매칭 순서:
    1. 정확 매칭: 학생 휴대폰, 학부모 휴대폰, OMR 코드
    2. 정확 매칭 0건일 때만 fuzzy fallback: Hamming 거리 ≤1 후보 검색.
       후보가 정확히 1명일 때만 자동 매칭, 그 외(0/2+)는 None → 수동 식별.

    시험 대상자는 명시 ExamEnrollment와 시험이 붙은 SessionEnrollment를 함께 본다.
    """
    lookup_tails = {tail for tail in tail8_variants(identifier) if len(tail) == 8}
    lookup_digit_tails = {tail for tail in lookup_tails if tail.isdigit()}

    exact_matches: list[int] = []
    fuzzy_candidates: list[tuple[int, int]] = []

    for enr in _candidate_enrollments(exam_id=exam_id, tenant=tenant):
        student = getattr(enr, "student", None)
        if not student:
            continue
        s_tails = tail8_variants(getattr(student, "phone", "") or "")
        p_tails = tail8_variants(getattr(student, "parent_phone", "") or "")
        o_tails = tail8_variants(getattr(student, "omr_code", "") or "")

        if lookup_tails & (s_tails | p_tails | o_tails):
            exact_matches.append(int(enr.id))
            continue

        student_digit_tails = {tail for tail in s_tails if len(tail) == 8 and tail.isdigit()}
        parent_digit_tails = {tail for tail in p_tails if len(tail) == 8 and tail.isdigit()}
        omr_digit_tails = {tail for tail in o_tails if len(tail) == 8 and tail.isdigit()}
        for candidate_tail in student_digit_tails | parent_digit_tails | omr_digit_tails:
            for lookup_tail in lookup_digit_tails:
                d = _hamming(candidate_tail, lookup_tail)
                if d <= 1:
                    fuzzy_candidates.append((int(enr.id), d))
                    break
            else:
                continue
            break

    if len(exact_matches) == 1:
        return exact_matches[0], "exact"
    if len(exact_matches) >= 2:
        return None, "none"

    fuzzy_unique = {eid for eid, _ in fuzzy_candidates}
    if len(fuzzy_unique) == 1:
        eid = next(iter(fuzzy_unique))
        logger.info(
            "omr_candidate_matching: fuzzy identifier match accepted | exam=%s | enr=%s",
            exam_id,
            eid,
        )
        return eid, "fuzzy"

    return None, "none"


def lock_exam_enrollment_candidate(*, tenant, exam_id: int, enrollment_id: int | None) -> bool:
    if not enrollment_id:
        return False

    from apps.domains.enrollment.models import SessionEnrollment
    from apps.domains.exams.models import ExamEnrollment

    if (
        ExamEnrollment.objects.select_for_update()
        .filter(
            exam_id=int(exam_id),
            exam__tenant=tenant,
            enrollment_id=int(enrollment_id),
            enrollment__tenant=tenant,
        )
        .exists()
    ):
        return True

    in_session = SessionEnrollment.objects.filter(
        tenant=tenant,
        session__exams__id=int(exam_id),
        session__exams__tenant=tenant,
        session__lecture__tenant=tenant,
        enrollment_id=int(enrollment_id),
        enrollment__tenant=tenant,
        enrollment__status="ACTIVE",
        enrollment__student__deleted_at__isnull=True,
    ).exists()
    if not in_session:
        return False

    ExamEnrollment.objects.get_or_create(
        exam_id=int(exam_id),
        enrollment_id=int(enrollment_id),
    )
    return (
        ExamEnrollment.objects.select_for_update()
        .filter(
            exam_id=int(exam_id),
            exam__tenant=tenant,
            enrollment_id=int(enrollment_id),
            enrollment__tenant=tenant,
        )
        .exists()
    )


def ensure_exam_enrollment_candidate(*, tenant, exam_id: int, enrollment_id: int) -> bool:
    from apps.domains.enrollment.models import Enrollment, SessionEnrollment
    from apps.domains.exams.models import ExamEnrollment

    enrollment_exists = Enrollment.objects.filter(
        id=int(enrollment_id),
        tenant=tenant,
    ).exists()
    if not enrollment_exists:
        return False

    in_exam = ExamEnrollment.objects.filter(
        exam_id=int(exam_id),
        exam__tenant=tenant,
        enrollment_id=int(enrollment_id),
        enrollment__tenant=tenant,
    ).exists()
    if in_exam:
        return True

    in_session = SessionEnrollment.objects.filter(
        tenant=tenant,
        session__exams__id=int(exam_id),
        session__exams__tenant=tenant,
        session__lecture__tenant=tenant,
        enrollment_id=int(enrollment_id),
        enrollment__tenant=tenant,
        enrollment__status="ACTIVE",
        enrollment__student__deleted_at__isnull=True,
    ).exists()
    if not in_session:
        return False

    ExamEnrollment.objects.get_or_create(
        exam_id=int(exam_id),
        enrollment_id=int(enrollment_id),
    )
    return True
