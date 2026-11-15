"""Cross-domain candidate lookups for submission matching views."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.db.models import F, Q, Value
from django.db.models.functions import Replace


@dataclass(frozen=True)
class CandidateRows:
    found: bool
    rows: list[dict[str, Any]]
    session_valid: bool = True


def _mask_phone_tail(phone: str | None) -> str:
    p = str(phone or "").replace("-", "").strip()
    if len(p) < 4:
        return ""
    return p[-4:]


def _candidate_rows_from_enrollments(enrollments) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for enrollment in enrollments:
        student = getattr(enrollment, "student", None)
        lecture = getattr(enrollment, "lecture", None)
        student_name = str(getattr(student, "name", "") or "") if student else ""
        student_phone = str(getattr(student, "phone", "") or "") if student else ""
        parent_phone = str(getattr(student, "parent_phone", "") or "") if student else ""
        lecture_title = str(getattr(lecture, "title", "") or "") if lecture else ""
        lecture_color = str(getattr(lecture, "color", "") or "") if lecture else ""
        lecture_chip_label = str(getattr(lecture, "chip_label", "") or "") if lecture else ""

        rows.append({
            "enrollment_id": int(enrollment.id),
            "student_name": student_name,
            "student_phone_last4": _mask_phone_tail(student_phone),
            "parent_phone_last4": _mask_phone_tail(parent_phone),
            "lecture_title": lecture_title or None,
            "lecture_color": lecture_color or None,
            "lecture_chip_label": lecture_chip_label or None,
        })
    return rows


def _filter_enrollments_for_query(qs, *, q: str, exact_phone_tail: bool = False):
    if q:
        digits = "".join(ch for ch in q if ch.isdigit())
        name_q = Q(student__name__icontains=q)
        phone_q = Q()
        if exact_phone_tail and len(digits) == 8:
            qs = qs.alias(
                student_phone_digits=Replace(Replace("student__phone", Value("-")), Value(" ")),
                parent_phone_digits=Replace(Replace("student__parent_phone", Value("-")), Value(" ")),
            )
            phone_q = Q(student_phone_digits__endswith=digits) | Q(parent_phone_digits__endswith=digits)
        elif len(digits) >= 3:
            phone_q = (
                Q(student__phone__icontains=digits)
                | Q(student__parent_phone__icontains=digits)
            )
        qs = qs.filter(name_q | phone_q) if phone_q.children else qs.filter(name_q)

    return qs.select_related("student", "lecture").order_by("student__name", "id")[:50]


def exam_review_session_is_linked(*, tenant, exam_id: int, session_id: int) -> bool:
    from apps.domains.lectures.models import Session

    return Session.objects.filter(
        id=session_id, lecture__tenant=tenant, exams__id=exam_id, exams__tenant=tenant,
    ).exists()


def exam_review_roster_filter(*, tenant, exam_id: int, session_id: int | None, include_attendance: bool) -> Q:
    from apps.domains.attendance.models import Attendance
    from apps.domains.enrollment.models import SessionEnrollment

    scope = {
        "tenant": tenant,
        "session__exams__id": exam_id,
        "session__exams__tenant": tenant,
        "session__lecture__tenant": tenant,
        "enrollment__lecture_id": F("session__lecture_id"),
    }
    if session_id is not None:
        scope["session_id"] = session_id
    roster = Q(id__in=SessionEnrollment.objects.filter(**scope).values("enrollment_id"))
    if include_attendance:
        roster |= Q(id__in=Attendance.objects.filter(**scope).values("enrollment_id"))
    return roster


def active_exam_review_enrollments(*, tenant):
    from apps.domains.enrollment.models import Enrollment

    return Enrollment.objects.filter(
        tenant=tenant, lecture__tenant=tenant, student__tenant=tenant,
        status="ACTIVE", student__deleted_at__isnull=True,
    )


def exam_candidate_rows(*, tenant, exam_id: int, q: str, session_id: int | None = None) -> CandidateRows:
    from apps.domains.exams.models import Exam, ExamEnrollment

    exam_id = int(exam_id)
    exam = Exam.objects.filter(
        id=exam_id,
        tenant=tenant,
        sessions__lecture__tenant=tenant,
    ).first()
    if not exam:
        return CandidateRows(found=False, rows=[])

    if session_id is not None and not exam_review_session_is_linked(tenant=tenant, exam_id=exam_id, session_id=session_id):
        return CandidateRows(found=True, rows=[], session_valid=False)

    exam_targets = ExamEnrollment.objects.filter(exam=exam).values("enrollment_id")
    has_exam_targets = exam_targets.exists()
    enrollments = active_exam_review_enrollments(tenant=tenant)
    if has_exam_targets:
        enrollments = enrollments.filter(id__in=exam_targets)
    if not has_exam_targets or session_id is not None:
        enrollments = enrollments.filter(exam_review_roster_filter(
            tenant=tenant, exam_id=exam_id, session_id=session_id, include_attendance=has_exam_targets,
        ))
    enrollments = _filter_enrollments_for_query(enrollments, q=q, exact_phone_tail=True)
    return CandidateRows(found=True, rows=_candidate_rows_from_enrollments(enrollments))


def homework_candidate_rows(*, tenant, homework_id: int, q: str) -> CandidateRows:
    from apps.domains.enrollment.models import Enrollment, SessionEnrollment
    from apps.domains.homework_results.models import Homework

    homework_id = int(homework_id)
    homework = Homework.objects.filter(id=homework_id, tenant=tenant).first()
    if not homework:
        return CandidateRows(found=False, rows=[])
    if isinstance(homework.meta, dict) and homework.meta.get("removed_from_session_at"):
        return CandidateRows(found=True, rows=[])

    session = homework.session
    if not session:
        return CandidateRows(found=True, rows=[])

    enrollment_ids = list(
        SessionEnrollment.objects
        .filter(session_id=session.id)
        .filter(enrollment__status="ACTIVE")
        .values_list("enrollment_id", flat=True)
        .distinct()
    )
    if not enrollment_ids:
        return CandidateRows(found=True, rows=[])

    enrollments = _filter_enrollments_for_query(Enrollment.objects.filter(
        id__in=enrollment_ids, tenant=tenant, student__deleted_at__isnull=True,
    ), q=q)
    return CandidateRows(found=True, rows=_candidate_rows_from_enrollments(enrollments))
