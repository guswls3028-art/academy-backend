"""Student-exam cross-domain dependencies.

The student app is a transport facade. Exam/submission internals stay behind
this support boundary while broader domain cutover is in progress.
"""

from __future__ import annotations

from datetime import timedelta

from django.db.models import Q
from django.utils import timezone


class StudentExamSubmitError(Exception):
    def __init__(self, detail: str, status_code: int):
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


def student_exam_queryset(student, tenant, *, include_upcoming_days: int = 0):
    from apps.domains.enrollment.selectors import active_enrollment_ids_for_student
    from apps.domains.exams.models import Exam

    now = timezone.now()
    latest_open_at = now
    if include_upcoming_days > 0:
        latest_open_at = now + timedelta(days=include_upcoming_days)
    enrollment_ids = active_enrollment_ids_for_student(tenant=tenant, student=student)
    if not enrollment_ids:
        return Exam.objects.none()
    return (
        Exam.objects.filter(
            exam_type=Exam.ExamType.REGULAR,
            exam_enrollments__enrollment_id__in=enrollment_ids,
            is_active=True,
        )
        .filter(
            Q(open_at__isnull=True) | Q(open_at__lte=latest_open_at),
            Q(close_at__isnull=True) | Q(close_at__gte=now),
        )
        .distinct()
        .order_by("open_at", "id")
    )


def submission_status_map_for_student_exams(*, tenant, student, exams) -> dict[int, dict[str, int | bool]]:
    exam_ids = [int(exam.id) for exam in exams]
    if not exam_ids:
        return {}

    from apps.domains.enrollment.selectors import active_enrollment_ids_for_student
    from apps.domains.submissions.models.submission import Submission

    enrollment_ids = active_enrollment_ids_for_student(tenant=tenant, student=student)
    if not enrollment_ids:
        return {}

    submission_status_map: dict[int, dict[str, int | bool]] = {}
    subs = Submission.objects.filter(
        enrollment_id__in=enrollment_ids,
        target_type=Submission.TargetType.EXAM,
        target_id__in=exam_ids,
    ).values_list("target_id", "status")
    for target_id, sub_status in subs:
        entry = submission_status_map.setdefault(
            int(target_id),
            {"has_result": False, "attempt_count": 0},
        )
        entry["attempt_count"] = int(entry["attempt_count"]) + 1
        if sub_status == Submission.Status.DONE:
            entry["has_result"] = True
    return submission_status_map


def student_exam_questions(exam):
    from apps.domains.exams.models import ExamQuestion
    from apps.domains.exams.services.template_resolver import resolve_template_exam

    template = resolve_template_exam(exam)
    return list(
        ExamQuestion.objects.filter(sheet__exam=template)
        .order_by("number")
        .values("id", "number", "score")
    )


def get_enrollment_for_student_exam(student, exam_id, tenant=None):
    from apps.domains.exams.models import ExamEnrollment

    if not student:
        return None, None
    if not tenant:
        return None, None
    if getattr(student, "tenant_id", None) != tenant.id:
        return None, None
    exam_enrollment = (
        ExamEnrollment.objects.filter(
            exam_id=int(exam_id),
            enrollment__student=student,
            enrollment__tenant=tenant,
            enrollment__status="ACTIVE",
        )
        .select_related("enrollment", "enrollment__tenant")
        .first()
    )
    if not exam_enrollment or not exam_enrollment.enrollment:
        return None, None
    return exam_enrollment.enrollment, getattr(exam_enrollment.enrollment, "tenant", None)


def create_online_exam_submission(
    *,
    request_user,
    request_student,
    tenant,
    exam,
    enrollment,
    answers,
):
    from django.db import IntegrityError, transaction

    from apps.domains.submissions.models import Submission
    from apps.domains.submissions.services.lifecycle import (
        IN_PROGRESS_STATUSES,
        supersede_done_submissions,
    )

    try:
        with transaction.atomic():
            prev_submissions = list(
                Submission.objects.select_for_update().filter(
                    enrollment_id=enrollment.id,
                    target_type=Submission.TargetType.EXAM,
                    target_id=int(exam.id),
                    status__in=[*IN_PROGRESS_STATUSES, Submission.Status.DONE],
                )
            )
            in_progress = [s for s in prev_submissions if s.status in IN_PROGRESS_STATUSES]
            done_submissions = [s for s in prev_submissions if s.status == Submission.Status.DONE]

            if in_progress:
                raise StudentExamSubmitError("이미 제출된 시험입니다.", 409)

            if done_submissions:
                allow_retake = getattr(exam, "allow_retake", False)
                max_attempts = getattr(exam, "max_attempts", 1) or 1
                attempt_count = len(done_submissions)
                if not allow_retake or attempt_count >= max_attempts:
                    raise StudentExamSubmitError("재응시가 허용되지 않는 시험입니다.", 409)
                supersede_done_submissions(
                    Submission.objects.filter(id__in=[s.id for s in done_submissions]),
                    actor="student.exam_submit.retake",
                )

            submission_user = request_student.user if request_student.user_id else request_user
            submission_meta = None
            if getattr(submission_user, "id", None) != getattr(request_user, "id", None):
                submission_meta = {"submitted_by_user_id": request_user.id}
            return Submission.objects.create(
                tenant=tenant,
                user=submission_user,
                enrollment_id=enrollment.id,
                target_type=Submission.TargetType.EXAM,
                target_id=int(exam.id),
                source=Submission.Source.ONLINE,
                payload={"answers": answers},
                meta=submission_meta,
                status=Submission.Status.SUBMITTED,
            )
    except IntegrityError as exc:
        if "unique_active_submission_per_target" in str(exc):
            raise StudentExamSubmitError("이미 제출된 시험입니다.", 409) from exc
        raise


def dispatch_student_exam_submission(submission) -> None:
    from apps.domains.submissions.services.dispatcher import dispatch_submission

    dispatch_submission(submission)
