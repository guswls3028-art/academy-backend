"""Current homework remediation for student and staff grade summaries."""

from apps.domains.progress.models import AssessmentCorrection, ClinicLink


def homework_remediation_keys(*, tenant, enrollment_ids, homework_ids):
    """Return the remediated and teacher-confirmed (homework, session, enrollment) keys."""
    if not enrollment_ids or not homework_ids:
        return set(), set()

    scope = {
        "tenant": tenant,
        "source_type": AssessmentCorrection.SourceType.HOMEWORK,
        "source_id__in": homework_ids,
        "enrollment_id__in": enrollment_ids,
    }
    teacher_confirmed = set(
        AssessmentCorrection.objects.filter(**scope, completed=True)
        .values_list("source_id", "session_id", "enrollment_id")
    )
    clinic_passed = set()
    seen = set()
    cycles = ClinicLink.objects.filter(**scope).order_by(
        "enrollment_id", "source_id", "session_id", "-cycle_no", "-id",
    ).values_list("source_id", "session_id", "enrollment_id", "resolved_at", "resolution_type")
    for homework_id, session_id, enrollment_id, resolved_at, resolution in cycles.iterator():
        key = (homework_id, session_id, enrollment_id)
        if key in seen:
            continue
        # An unresolved current cycle still supersedes every historical pass for this assignment.
        seen.add(key)
        if not resolved_at:
            continue
        if resolution == ClinicLink.ResolutionType.MANUAL_OVERRIDE:
            teacher_confirmed.add(key)
        elif resolution in (ClinicLink.ResolutionType.HOMEWORK_PASS, ClinicLink.ResolutionType.EXAM_PASS):
            clinic_passed.add(key)
    return teacher_confirmed | clinic_passed, teacher_confirmed
