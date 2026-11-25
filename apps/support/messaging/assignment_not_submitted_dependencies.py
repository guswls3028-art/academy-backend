"""Cross-domain read helpers for assignment-not-submitted notifications."""

from __future__ import annotations

from datetime import date
from typing import Any


def sessions_for_assignment_not_submitted(
    *,
    target_date: date,
    tenant_id: int | None = None,
) -> list[Any]:
    from apps.domains.lectures.models import Session

    sessions = Session.objects.filter(date=target_date).select_related("lecture")
    if tenant_id:
        sessions = sessions.filter(lecture__tenant_id=tenant_id)
    return list(sessions)


def homeworks_for_session(session: Any) -> list[Any]:
    from apps.support.submissions.dependencies import staff_homework_file_targets

    return list(staff_homework_file_targets(tenant=session.lecture.tenant).filter(session=session))


def missing_homework_assignments(*, tenant, homework, student_ids=None, lock=False):
    """Resolve the assigned roster against persisted file evidence and grading."""
    from apps.domains.homework.models import HomeworkAssignment
    from apps.domains.homework_results.models import HomeworkScore
    from apps.domains.progress.models import AssessmentCorrection
    from apps.support.submissions.dependencies import homework_submission_revisions
    from apps.support.submissions.homework_acceptance_queries import homework_accepted_keys

    assignments = HomeworkAssignment.objects.filter(
        tenant=tenant, homework=homework, session_id=homework.session_id,
        enrollment__tenant=tenant, enrollment__lecture_id=homework.session.lecture_id,
        enrollment__status="ACTIVE", enrollment__student__tenant=tenant,
        enrollment__student__deleted_at__isnull=True,
    ).select_related("enrollment__student").order_by("pk")
    if student_ids is not None:
        assignments = assignments.filter(enrollment__student_id__in=student_ids)
    if lock:
        assignments = assignments.select_for_update(of=("self",))
    rows = list(assignments)
    enrollment_ids = [row.enrollment_id for row in rows]
    if not enrollment_ids:
        return []
    target = dict(tenant=tenant, enrollment_ids=enrollment_ids, homework_ids=[homework.pk])
    excluded = {eid for eid, _ in homework_submission_revisions(**target)}
    excluded.update(eid for _, _, eid in homework_accepted_keys(**target))
    excluded.update(AssessmentCorrection.objects.filter(
        tenant=tenant, session_id=homework.session_id, enrollment_id__in=enrollment_ids,
        source_type=AssessmentCorrection.SourceType.HOMEWORK, source_id=homework.pk, completed=True,
    ).values_list("enrollment_id", flat=True))
    scores = HomeworkScore.objects.filter(
        homework=homework, session_id=homework.session_id, enrollment_id__in=enrollment_ids,
    ).values_list("enrollment_id", "score", "passed", "meta")
    for eid, score, passed, meta in scores:
        if score is not None or passed or (isinstance(meta, dict) and meta.get("status") == HomeworkScore.MetaStatus.VIDEO_MAKEUP):
            excluded.add(eid)
    return [row for row in rows if row.enrollment_id not in excluded]


def missing_homework_target(tenant, homework_id):
    from apps.support.submissions.dependencies import staff_homework_file_targets

    return staff_homework_file_targets(tenant=tenant).select_related("session__lecture").filter(pk=homework_id).first()


def missing_homework_confirmation_valid(tenant, payload, *, confirmed_guards=None):
    from apps.domains.messaging.services.recipients import normalize_phone

    homework_id = payload.get("homework_id")
    recipients = payload.get("recipients")
    if type(homework_id) is not int or not recipients:
        return False
    homework = missing_homework_target(tenant, homework_id)
    if not homework:
        return False
    reviewed = {recipient["student_id"]: recipient["phone"] for recipient in recipients}
    still_missing = missing_homework_assignments(tenant=tenant, homework=homework, student_ids=reviewed, lock=True)
    current = {}
    for row in still_missing:
        student = row.enrollment.student
        current[student.pk] = normalize_phone(student.parent_phone if payload["send_to"] == "parent" else student.phone)
    if reviewed != current:
        return False
    if confirmed_guards is not None:
        for row in still_missing:
            student_id = row.enrollment.student_id
            guard = confirmed_guards.setdefault(student_id, {
                "homework_id": homework.pk, "session_id": homework.session_id,
                "student_id": student_id, "assignment_ids": [],
            })
            guard["assignment_ids"].append(row.pk)
    return True
