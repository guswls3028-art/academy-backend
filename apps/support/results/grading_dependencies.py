"""Cross-domain dependencies for result grading and scope guards."""

from __future__ import annotations

from typing import Any

from django.shortcuts import get_object_or_404


def get_enrollment_for_tenant(*, enrollment_id: int, tenant: Any) -> Any | None:
    from apps.domains.enrollment.models import Enrollment

    return Enrollment.objects.filter(id=int(enrollment_id), tenant=tenant).first()


def exam_enrollment_exists(*, exam_id: int, enrollment_id: int) -> bool:
    from apps.domains.exams.models import ExamEnrollment

    return ExamEnrollment.objects.filter(
        exam_id=int(exam_id),
        enrollment_id=int(enrollment_id),
    ).exists()


def materialize_exam_enrollment_from_linked_session(*, exam: Any, enrollment_id: int) -> bool:
    from apps.domains.enrollment.models import SessionEnrollment
    from apps.domains.exams.models import ExamEnrollment

    in_linked_session = SessionEnrollment.objects.filter(
        tenant=exam.tenant,
        session__exams__id=exam.id,
        session__exams__tenant=exam.tenant,
        session__lecture__tenant=exam.tenant,
        enrollment_id=int(enrollment_id),
        enrollment__tenant=exam.tenant,
        enrollment__status="ACTIVE",
        enrollment__student__deleted_at__isnull=True,
    ).exists()
    if not in_linked_session:
        return False

    ExamEnrollment.objects.get_or_create(
        exam_id=exam.id,
        enrollment_id=int(enrollment_id),
    )
    return True


def get_active_submission_enrollment(*, submission: Any) -> Any | None:
    from apps.domains.enrollment.models import Enrollment

    enrollment_id = getattr(submission, "enrollment_id", None)
    if not enrollment_id:
        return None
    return (
        Enrollment.objects
        .filter(
            id=int(enrollment_id),
            tenant_id=int(submission.tenant_id),
            status="ACTIVE",
            student__deleted_at__isnull=True,
        )
        .select_related("student", "lecture")
        .first()
    )


def submission_enrollment_assigned_to_exam(*, exam_id: int, enrollment_id: int, tenant_id: int) -> bool:
    from apps.domains.exams.models import ExamEnrollment

    return ExamEnrollment.objects.filter(
        exam_id=int(exam_id),
        enrollment_id=int(enrollment_id),
        enrollment__tenant_id=int(tenant_id),
    ).exists()


def get_submission_for_grading(*, submission_id: int) -> Any | None:
    from apps.domains.submissions.models import Submission

    return Submission.objects.filter(id=int(submission_id)).only(
        "id",
        "source",
        "meta",
    ).first()


def is_omr_manual_review_required(submission: Any) -> bool:
    if not submission:
        return False

    from apps.domains.submissions.models import Submission

    return bool(
        submission.source == Submission.Source.OMR_SCAN
        and isinstance(submission.meta, dict)
        and isinstance(submission.meta.get("manual_review"), dict)
        and submission.meta["manual_review"].get("required") is True
    )


def dispatch_progress_pipeline(**kwargs: Any) -> Any:
    from apps.domains.progress.dispatcher import dispatch_progress_pipeline as _dispatch

    return _dispatch(**kwargs)


def get_submission_for_result_sync(*, submission_id: int) -> Any:
    from apps.domains.submissions.models import Submission

    return get_object_or_404(
        Submission.objects.select_related("user"),
        id=int(submission_id),
    )


def get_exam_for_result_sync(*, exam_id: int) -> Any:
    from apps.domains.exams.models import Exam

    return get_object_or_404(Exam, id=int(exam_id))
