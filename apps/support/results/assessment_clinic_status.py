"""Current clinic decisions for assessment grade projections."""

from django.db.models import F, Window
from django.db.models.functions import RowNumber

from apps.domains.progress.models import AssessmentCorrection, ClinicLink


def current_clinic_resolution_rows(
    *, tenant, source_type, enrollment_ids, source_ids, session_ids=None, extra_fields=(),
):
    """Read the newest cycle for each exact assignment, even when it is unresolved."""
    if not enrollment_ids or not source_ids or (session_ids is not None and not session_ids):
        return []

    links = ClinicLink.objects.filter(
        tenant=tenant,
        enrollment__tenant=tenant,
        session__lecture__tenant=tenant,
        session__lecture_id=F("enrollment__lecture_id"),
        source_type=source_type,
        source_id__in=source_ids,
        enrollment_id__in=enrollment_ids,
    )
    if session_ids is not None:
        links = links.filter(session_id__in=session_ids)

    # Filter the resolution type only after selecting the newest cycle.
    latest_links = links.order_by().annotate(
        cycle_rank=Window(
            expression=RowNumber(),
            partition_by=[F("source_id"), F("session_id"), F("enrollment_id")],
            order_by=[F("cycle_no").desc(), F("id").desc()],
        ),
    ).filter(cycle_rank=1)
    return list(latest_links.values(
        "source_id", "session_id", "enrollment_id", "resolved_at", "resolution_type", *extra_fields,
    ))


def homework_remediation_keys(*, tenant, enrollment_ids, homework_ids):
    """Return the remediated and teacher-confirmed (homework, session, enrollment) keys."""
    if not enrollment_ids or not homework_ids:
        return set(), set()

    source = AssessmentCorrection.SourceType.HOMEWORK
    teacher_confirmed = set(
        AssessmentCorrection.objects.filter(
            tenant=tenant, source_type=source, source_id__in=homework_ids,
            enrollment_id__in=enrollment_ids, completed=True,
        ).values_list("source_id", "session_id", "enrollment_id")
    )
    clinic_passed = set()
    for link in current_clinic_resolution_rows(
        tenant=tenant, source_type=source, enrollment_ids=enrollment_ids, source_ids=homework_ids,
    ):
        if not link["resolved_at"]:
            continue
        key = (link["source_id"], link["session_id"], link["enrollment_id"])
        resolution = link["resolution_type"]
        if resolution == ClinicLink.ResolutionType.MANUAL_OVERRIDE:
            teacher_confirmed.add(key)
        elif resolution in (ClinicLink.ResolutionType.HOMEWORK_PASS, ClinicLink.ResolutionType.EXAM_PASS):
            clinic_passed.add(key)
    return teacher_confirmed | clinic_passed, teacher_confirmed
