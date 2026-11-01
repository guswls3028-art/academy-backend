"""Materialize a legacy exam's eligible roster without shrinking it on first use."""

from django.db import transaction
from django.db.models import F


def linked_exam_roster_ids(*, tenant, exam_id: int, include_attendance: bool = False) -> set[int]:
    from apps.domains.enrollment.models import SessionEnrollment

    if tenant is None:
        return set()
    scope = {
        "tenant": tenant,
        "session__exams__id": int(exam_id),
        "session__exams__tenant": tenant,
        "session__lecture__tenant": tenant,
        "enrollment__tenant": tenant,
        "enrollment__student__tenant": tenant,
        "enrollment__lecture_id": F("session__lecture_id"),
        "enrollment__status": "ACTIVE",
        "enrollment__student__deleted_at__isnull": True,
    }
    ids = set(SessionEnrollment.objects.filter(**scope).values_list("enrollment_id", flat=True))
    if include_attendance:
        from apps.domains.attendance.models import Attendance

        ids.update(Attendance.objects.filter(**scope).values_list("enrollment_id", flat=True))
    return ids


@transaction.atomic
def ensure_legacy_exam_roster_candidate(
    *, tenant, exam_id: int, enrollment_id: int, include_attendance: bool = False,
) -> bool:
    from apps.domains.exams.models import Exam, ExamEnrollment

    if tenant is None:
        return False
    # Share the roster editor's exam lock. Callers that also lock submissions
    # must acquire their Exam -> Session scope before the submission row.
    exam = Exam.objects.select_for_update(no_key=True).filter(
        id=int(exam_id), tenant=tenant,
    ).first()
    if exam is None:
        return False
    targets = ExamEnrollment.objects.filter(exam=exam)
    if targets.exists():
        # A teacher-selected subset is authoritative, even when the requested
        # student is on a linked session roster. Never expand an existing list.
        return targets.filter(
            enrollment_id=int(enrollment_id), enrollment__tenant=tenant,
            enrollment__student__tenant=tenant, enrollment__status="ACTIVE",
            enrollment__student__deleted_at__isnull=True,
        ).exists()

    candidate_ids = linked_exam_roster_ids(
        tenant=tenant, exam_id=int(exam.id), include_attendance=include_attendance,
    )
    if int(enrollment_id) not in candidate_ids:
        return False
    if not include_attendance:
        # OMR's first selected student must satisfy its session-only fallback,
        # but freezing that roster must not remove attendance-only students'
        # existing right to view and enter scores for the same linked exam.
        candidate_ids = linked_exam_roster_ids(
            tenant=tenant, exam_id=int(exam.id), include_attendance=True,
        )
    # Existing attendance/enrollment writers may commit the same eligible pair
    # without taking the Exam lock. Keep their row and insert the rest of the
    # snapshot; a duplicate must not abort this student's OMR transaction.
    ExamEnrollment.objects.bulk_create([
        ExamEnrollment(exam_id=exam.id, enrollment_id=candidate_id)
        for candidate_id in sorted(candidate_ids)
    ], ignore_conflicts=True)
    return True
