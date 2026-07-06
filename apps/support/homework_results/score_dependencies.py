"""Cross-domain helpers for homework score updates."""

from __future__ import annotations

from typing import Any


def validate_enrollment_belongs_to_tenant(enrollment_id: int, tenant: Any) -> None:
    from apps.domains.results.guards.enrollment_tenant_guard import (
        validate_enrollment_belongs_to_tenant as validate,
    )

    validate(enrollment_id, tenant)


def calc_homework_passed_and_clinic(*, session: Any, score: float | None, max_score: float | None):
    from apps.domains.homework.utils.homework_policy import (
        calc_homework_passed_and_clinic as calculate,
    )

    return calculate(session=session, score=score, max_score=max_score)


def latest_homework_submission(*, enrollment_id: int, homework_id: int):
    from apps.domains.submissions.models import Submission

    return (
        Submission.objects
        .filter(
            enrollment_id=enrollment_id,
            target_type="homework",
            target_id=homework_id,
        )
        .order_by("-id")
        .first()
    )


def dispatch_progress_pipeline(*, submission_id: int) -> None:
    from apps.domains.progress.dispatcher import dispatch_progress_pipeline as dispatch

    dispatch(submission_id=submission_id)


def homework_assignment_exists(
    *,
    tenant: Any,
    homework: Any,
    session: Any,
    enrollment_id: int,
) -> bool:
    from apps.domains.homework.models import HomeworkAssignment

    return HomeworkAssignment.objects.filter(
        tenant=tenant,
        homework=homework,
        session=session,
        enrollment_id=enrollment_id,
    ).exists()


def sync_homework_clinic_link(
    *,
    enrollment_id: int,
    session: Any,
    homework_id: int,
    passed: bool,
    score: float | None,
    max_score: float | None,
) -> None:
    from django.db import IntegrityError as DjangoIntegrityError
    from django.db.models import Max

    from apps.domains.enrollment.models import Enrollment
    from apps.domains.progress.models import ClinicLink
    from apps.domains.progress.services.clinic_resolution_service import (
        ClinicResolutionService,
    )

    if passed:
        ClinicResolutionService.resolve_by_homework_pass(
            enrollment_id=enrollment_id,
            session_id=session.id,
            homework_id=homework_id,
            score=score,
            max_score=max_score,
        )
        return

    existing_unresolved = ClinicLink.objects.filter(
        enrollment_id=enrollment_id,
        session=session,
        source_type="homework",
        source_id=homework_id,
        resolved_at__isnull=True,
    ).exists()
    if existing_unresolved:
        return

    max_cycle = (
        ClinicLink.objects
        .filter(
            enrollment_id=enrollment_id,
            session=session,
            source_type="homework",
            source_id=homework_id,
        )
        .aggregate(Max("cycle_no"))["cycle_no__max"]
        or 0
    )
    tenant_id = (
        Enrollment.objects
        .filter(id=enrollment_id)
        .values_list("tenant_id", flat=True)
        .first()
    )
    try:
        ClinicLink.objects.create(
            enrollment_id=enrollment_id,
            session=session,
            source_type="homework",
            source_id=homework_id,
            reason=ClinicLink.Reason.AUTO_FAILED,
            is_auto=True,
            approved=False,
            cycle_no=max(max_cycle + 1, 1),
            tenant_id=tenant_id,
            meta={
                "kind": "HOMEWORK_FAILED",
                "homework_id": homework_id,
                "score": score,
                "max_score": max_score,
            },
        )
    except DjangoIntegrityError:
        pass
