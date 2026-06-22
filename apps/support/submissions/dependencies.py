"""Cross-domain helpers for submission-facing orchestration.

Submission views/services should keep status transitions in the submissions
lifecycle while cross-domain lookups stay behind this support boundary.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExamEnrollmentCandidate:
    ok: bool
    detail: str = ""
    should_create: bool = False


def grade_submission_objective(submission_id: int):
    from apps.domains.results.services.grading_service import grade_submission

    return grade_submission(int(submission_id))


def complete_submission_after_auto_grade(submission, *, actor: str) -> None:
    from django.core.exceptions import ValidationError

    from apps.domains.submissions.models import Submission
    from apps.domains.submissions.services.lifecycle import (
        can_mark_done,
        mark_done,
        mark_grading,
    )

    if submission.status == Submission.Status.ANSWERS_READY:
        mark_grading(submission, actor=actor)
        mark_done(submission, actor=actor)
        return

    if can_mark_done(submission.status):
        mark_done(submission, actor=actor)
        return

    logger.error(
        "Submission %s in status '%s' cannot transition to 'done'; "
        "aborting grading to preserve data consistency.",
        submission.id,
        submission.status,
    )
    raise ValidationError(
        f"Submission {submission.id} in status '{submission.status}' "
        f"cannot be graded - invalid state for transition to 'done'."
    )


def regrade_exam_submissions(*, tenant, exam_id: int, actor: str) -> dict[str, Any]:
    from django.db import transaction

    from apps.domains.submissions.models import Submission
    from apps.domains.submissions.services.lifecycle import reopen_for_regrade

    regradable_statuses = {
        Submission.Status.DONE,
        Submission.Status.ANSWERS_READY,
    }
    submissions = list(
        Submission.objects.filter(
            tenant=tenant,
            target_type=Submission.TargetType.EXAM,
            target_id=int(exam_id),
        )
        .exclude(status=Submission.Status.SUPERSEDED)
        .order_by("id")
        .values_list("id", "status")
    )

    graded = 0
    skipped = 0
    failed: list[dict[str, object]] = []

    for submission_id, current_status in submissions:
        if current_status not in regradable_statuses:
            skipped += 1
            continue
        try:
            if current_status != Submission.Status.ANSWERS_READY:
                with transaction.atomic():
                    submission = Submission.objects.select_for_update().get(id=int(submission_id))
                    if submission.status != Submission.Status.ANSWERS_READY:
                        reopen_for_regrade(submission, actor=actor)
            grade_submission_objective(int(submission_id))
            graded += 1
        except Exception as exc:
            failed.append(
                {
                    "submission_id": int(submission_id),
                    "status": str(current_status),
                    "detail": str(exc) or exc.__class__.__name__,
                }
            )

    return {
        "exam_id": int(exam_id),
        "total": len(submissions),
        "graded": graded,
        "skipped": skipped,
        "failed": failed,
    }


def target_belongs_to_tenant(target_type, target_id, tenant) -> bool:
    from apps.domains.submissions.models import Submission

    try:
        target_id_i = int(target_id)
        if target_type == Submission.TargetType.EXAM:
            from apps.domains.exams.models import Exam

            return Exam.objects.filter(
                id=target_id_i,
                tenant=tenant,
                sessions__lecture__tenant=tenant,
            ).exists()
        if target_type == Submission.TargetType.HOMEWORK:
            from apps.domains.homework_results.models import Homework

            return (
                Homework.objects.filter(
                    id=target_id_i,
                    session__lecture__tenant=tenant,
                )
                .exclude(meta__removed_from_session_at__isnull=False)
                .exists()
            )
    except Exception:
        return False
    return False


def enrollment_belongs_to_tenant(*, enrollment_id, tenant) -> bool:
    from apps.domains.enrollment.models import Enrollment

    return Enrollment.objects.filter(id=enrollment_id, tenant=tenant).exists()


def student_owns_enrollment(*, enrollment_id, student, tenant) -> bool:
    from apps.domains.enrollment.models import Enrollment

    return Enrollment.objects.filter(
        id=enrollment_id,
        student=student,
        tenant=tenant,
    ).exists()


def target_enrollment_assignment_exists(
    target_type,
    target_id,
    enrollment_id,
    tenant,
    *,
    ensure_exam_enrollment: bool = False,
) -> bool:
    from apps.domains.submissions.models import Submission

    try:
        target_id_i = int(target_id)
        enrollment_id_i = int(enrollment_id)
    except (TypeError, ValueError):
        return False

    from apps.domains.enrollment.models import Enrollment, SessionEnrollment

    enrollment = (
        Enrollment.objects.filter(
            id=enrollment_id_i,
            tenant=tenant,
            status="ACTIVE",
            student__deleted_at__isnull=True,
        )
        .select_related("lecture")
        .first()
    )
    if not enrollment:
        return False

    if target_type == Submission.TargetType.EXAM:
        from apps.domains.exams.models import ExamEnrollment

        in_exam = ExamEnrollment.objects.filter(
            exam_id=target_id_i,
            exam__tenant=tenant,
            enrollment_id=enrollment_id_i,
            enrollment__tenant=tenant,
        ).exists()
        if in_exam:
            return True

        in_session = SessionEnrollment.objects.filter(
            tenant=tenant,
            session__exams__id=target_id_i,
            session__exams__tenant=tenant,
            enrollment_id=enrollment_id_i,
            enrollment__status="ACTIVE",
            enrollment__student__deleted_at__isnull=True,
        ).exists()
        if in_session and ensure_exam_enrollment:
            ExamEnrollment.objects.get_or_create(
                exam_id=target_id_i,
                enrollment_id=enrollment_id_i,
            )
        return in_session

    if target_type == Submission.TargetType.HOMEWORK:
        from apps.domains.homework_results.models import Homework

        return (
            Homework.objects.filter(
                id=target_id_i,
                session__lecture_id=enrollment.lecture_id,
                session__lecture__tenant=tenant,
            )
            .exclude(meta__removed_from_session_at__isnull=False)
            .filter(
                assignments__tenant=tenant,
                assignments__enrollment_id=enrollment_id_i,
            )
            .exists()
        )

    return False


def validate_exam_enrollment_candidate(
    *,
    tenant,
    exam_id: int,
    enrollment_id: int,
) -> ExamEnrollmentCandidate:
    from apps.domains.enrollment.models import Enrollment, SessionEnrollment
    from apps.domains.exams.models import ExamEnrollment

    if not Enrollment.objects.filter(id=enrollment_id, tenant=tenant).exists():
        return ExamEnrollmentCandidate(
            ok=False,
            detail=f"enrollment_id={enrollment_id}는 현재 학원의 학생이 아닙니다.",
        )

    if not exam_id:
        return ExamEnrollmentCandidate(ok=True)

    in_exam = ExamEnrollment.objects.filter(
        exam_id=exam_id,
        enrollment_id=enrollment_id,
    ).exists()
    if in_exam:
        return ExamEnrollmentCandidate(ok=True)

    in_session = SessionEnrollment.objects.filter(
        tenant=tenant,
        session__exams__id=exam_id,
        enrollment_id=enrollment_id,
        enrollment__status="ACTIVE",
        enrollment__student__deleted_at__isnull=True,
    ).exists()
    if not in_session:
        return ExamEnrollmentCandidate(
            ok=False,
            detail="해당 시험에 등록되지 않은 학생입니다.",
        )
    return ExamEnrollmentCandidate(ok=True, should_create=True)


def create_exam_enrollment_assignment(*, exam_id: int, enrollment_id: int) -> bool:
    from apps.domains.exams.models import ExamEnrollment

    _, created = ExamEnrollment.objects.get_or_create(
        exam_id=int(exam_id),
        enrollment_id=int(enrollment_id),
    )
    return bool(created)


def exam_question_number_by_id(*, tenant, question_ids: list[int]) -> dict[int, int]:
    if not question_ids:
        return {}

    from apps.domains.exams.models import ExamQuestion

    return {
        int(qid): int(number)
        for qid, number in ExamQuestion.objects.filter(
            id__in=question_ids,
            sheet__exam__tenant=tenant,
        ).values_list("id", "number")
    }


def allowed_manual_exam_question_ids(*, tenant, exam_id: int) -> set[int] | None:
    from apps.domains.exams.models import Exam, ExamQuestion

    exam = (
        Exam.objects.filter(id=int(exam_id or 0), tenant=tenant)
        .select_related("template_exam")
        .first()
    )
    if not exam:
        return None

    sheet_exam_ids = [exam.id]
    if exam.template_exam_id:
        sheet_exam_ids.append(exam.template_exam_id)

    return set(
        ExamQuestion.objects.filter(
            sheet__exam_id__in=sheet_exam_ids,
            sheet__exam__tenant=tenant,
        ).values_list("id", flat=True)
    )


def get_synced_exam_score(*, tenant, target_id: int, enrollment_id: int) -> tuple[float | None, float | None]:
    try:
        from apps.domains.results.models import Result

        result = (
            Result.objects.filter(
                target_type="exam",
                target_id=int(target_id),
                enrollment_id=int(enrollment_id),
                enrollment__tenant=tenant,
            )
            .only("total_score", "max_score")
            .order_by("-id")
            .first()
        )
        if result:
            return float(result.total_score or 0.0), float(result.max_score or 0.0)
    except Exception:
        return None, None
    return None, None
