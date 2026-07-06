"""Cross-domain candidate lookups for submission matching views."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.db.models import Q


@dataclass(frozen=True)
class CandidateRows:
    found: bool
    rows: list[dict[str, Any]]


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


def _filter_enrollments_for_query(*, enrollment_ids: list[int], tenant, q: str):
    from apps.domains.enrollment.models import Enrollment

    qs = (
        Enrollment.objects
        .filter(id__in=enrollment_ids, tenant=tenant)
        .filter(student__deleted_at__isnull=True)
        .select_related("student", "lecture")
    )

    if q:
        digits = "".join(ch for ch in q if ch.isdigit())
        name_q = Q(student__name__icontains=q)
        phone_q = Q()
        if digits and len(digits) >= 3:
            phone_q = (
                Q(student__phone__icontains=digits)
                | Q(student__parent_phone__icontains=digits)
            )
        qs = qs.filter(name_q | phone_q) if phone_q.children else qs.filter(name_q)

    return qs.order_by("student__name", "id")[:50]


def exam_candidate_rows(*, tenant, exam_id: int, q: str) -> CandidateRows:
    from apps.domains.enrollment.models import SessionEnrollment
    from apps.domains.exams.models import Exam, ExamEnrollment

    exam_id = int(exam_id)
    exam = Exam.objects.filter(
        id=exam_id,
        sessions__lecture__tenant=tenant,
    ).first()
    if not exam:
        return CandidateRows(found=False, rows=[])

    enrollment_ids = list(
        ExamEnrollment.objects
        .filter(exam_id=exam_id)
        .values_list("enrollment_id", flat=True)
    )

    if not enrollment_ids:
        session_ids = list(exam.sessions.values_list("id", flat=True))
        enrollment_ids = list(
            SessionEnrollment.objects
            .filter(session_id__in=session_ids)
            .filter(enrollment__status="ACTIVE")
            .values_list("enrollment_id", flat=True)
            .distinct()
        )

    if not enrollment_ids:
        return CandidateRows(found=True, rows=[])

    enrollments = _filter_enrollments_for_query(
        enrollment_ids=enrollment_ids,
        tenant=tenant,
        q=q,
    )
    return CandidateRows(found=True, rows=_candidate_rows_from_enrollments(enrollments))


def homework_candidate_rows(*, tenant, homework_id: int, q: str) -> CandidateRows:
    from apps.domains.enrollment.models import SessionEnrollment
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

    enrollments = _filter_enrollments_for_query(
        enrollment_ids=enrollment_ids,
        tenant=tenant,
        q=q,
    )
    return CandidateRows(found=True, rows=_candidate_rows_from_enrollments(enrollments))
