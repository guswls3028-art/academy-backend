"""Cross-domain write boundary for result-owned clinic target actions."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ClinicWaiverOutcome:
    code: str
    clinic_link_id: int | None = None


def waive_explicit_missing_exam_target(
    *,
    tenant,
    session_id: int,
    enrollment_id: int,
    exam_id: int,
    result_id: int,
    user_id: int,
    memo: str,
) -> ClinicWaiverOutcome:
    """Validate exact roster/source ownership and create or reuse a WAIVED link."""
    from apps.domains.enrollment.models import SessionEnrollment
    from apps.domains.exams.models import Exam
    from apps.domains.progress.models import ClinicLink
    from apps.domains.progress.services.clinic_resolution_service import (
        ClinicResolutionService,
    )

    roster_exists = SessionEnrollment.objects.filter(
        tenant=tenant,
        session_id=session_id,
        session__lecture__tenant=tenant,
        session__exams__id=exam_id,
        enrollment_id=enrollment_id,
        enrollment__tenant=tenant,
        enrollment__status="ACTIVE",
    ).exists()
    exam_exists = Exam.objects.filter(
        id=exam_id,
        tenant=tenant,
        exam_type=Exam.ExamType.REGULAR,
        is_active=True,
    ).exists()
    if not roster_exists or not exam_exists:
        return ClinicWaiverOutcome("NOT_FOUND")

    links = list(
        ClinicLink.objects.select_for_update()
        .filter(
            tenant=tenant,
            session_id=session_id,
            enrollment_id=enrollment_id,
            source_type="exam",
            source_id=exam_id,
        )
        .order_by("-cycle_no", "-id")
    )
    if links and links[0].resolved_at:
        if links[0].resolution_type == ClinicLink.ResolutionType.WAIVED:
            return ClinicWaiverOutcome("WAIVED", int(links[0].id))
        return ClinicWaiverOutcome("ALREADY_RESOLVED", int(links[0].id))

    link = links[0] if links else ClinicLink.objects.create(
        tenant=tenant,
        session_id=session_id,
        enrollment_id=enrollment_id,
        source_type="exam",
        source_id=exam_id,
        reason=ClinicLink.Reason.AUTO_FAILED,
        is_auto=True,
        approved=True,
        cycle_no=1,
        meta={
            "kind": "EXAM_NOT_SUBMITTED",
            "exam_id": exam_id,
            "result_id": result_id,
        },
    )
    waived = ClinicResolutionService.waive(
        clinic_link_id=link.id,
        user_id=user_id,
        memo=memo,
    )
    if not waived:
        return ClinicWaiverOutcome("FAILED", int(link.id))
    return ClinicWaiverOutcome("CREATED", int(waived.id))
