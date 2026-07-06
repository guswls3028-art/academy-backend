"""Shared cross-domain dependencies for admin exam result endpoints."""

from __future__ import annotations

from typing import Any

from django.shortcuts import get_object_or_404


def regular_active_exam_with_session_exists(*, exam_id: int, tenant: Any) -> bool:
    from apps.domains.exams.models import Exam

    return Exam.objects.filter(
        id=exam_id,
        tenant=tenant,
        exam_type=Exam.ExamType.REGULAR,
        is_active=True,
        sessions__lecture__tenant=tenant,
    ).exists()


def get_regular_active_exam_for_tenant_or_none(*, exam_id: int, tenant: Any) -> Any | None:
    from apps.domains.exams.models import Exam

    return Exam.objects.filter(
        id=exam_id,
        tenant=tenant,
        exam_type=Exam.ExamType.REGULAR,
        is_active=True,
    ).first()


def get_regular_active_exam_for_tenant(*, exam_id: int, tenant: Any) -> Any:
    from apps.domains.exams.models import Exam

    return get_object_or_404(
        Exam,
        id=exam_id,
        tenant=tenant,
        exam_type=Exam.ExamType.REGULAR,
        is_active=True,
        sessions__lecture__tenant=tenant,
    )


def get_enrollment_for_tenant(*, enrollment_id: int, tenant: Any) -> Any | None:
    from apps.domains.enrollment.models import Enrollment

    return Enrollment.objects.filter(id=enrollment_id, tenant=tenant).first()


def get_enrollments_for_tenant_by_id(*, enrollment_ids: list[int], tenant: Any) -> dict[int, Any]:
    from apps.domains.enrollment.models import Enrollment

    return {
        int(enrollment.id): enrollment
        for enrollment in Enrollment.objects
        .filter(id__in=enrollment_ids, tenant=tenant)
        .select_related("student", "lecture")
    }


def get_latest_exam_submission_id(*, enrollment_id: int, exam_id: int) -> int | None:
    from apps.domains.submissions.models import Submission

    submission = (
        Submission.objects.filter(
            enrollment_id=enrollment_id,
            target_type=Submission.TargetType.EXAM,
            target_id=exam_id,
        )
        .order_by("-id")
        .first()
    )
    return int(submission.id) if submission else None


def get_latest_session_submission_id(*, enrollment_id: int, session_id: int) -> int | None:
    from apps.domains.submissions.models import Submission

    submission = (
        Submission.objects
        .filter(enrollment_id=enrollment_id, session_id=session_id)
        .order_by("-id")
        .first()
    )
    return int(submission.id) if submission else None


def get_submission_status_by_id_for_tenant(*, submission_ids: list[int], tenant: Any) -> dict[int, Any]:
    from apps.domains.submissions.models import Submission

    if not submission_ids:
        return {}
    return {
        int(submission.id): submission.status
        for submission in Submission.objects.filter(id__in=submission_ids, tenant=tenant)
    }


def dispatch_progress_pipeline(**kwargs: Any) -> Any:
    from apps.domains.progress.dispatcher import dispatch_progress_pipeline as _dispatch

    return _dispatch(**kwargs)
