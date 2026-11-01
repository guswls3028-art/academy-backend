"""Student-exam cross-domain dependencies.

The student app is a transport facade. Exam/submission internals stay behind
this support boundary while broader domain cutover is in progress.
"""

from __future__ import annotations

import logging
import uuid
from datetime import timedelta

from django.db.models import F, Max, Q, Subquery, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from apps.support.student_app.learning_todo_policy import ongoing_lecture_filter

logger = logging.getLogger(__name__)
MISSING_EXAM_ENROLLMENT: tuple[None, None] = (None, None)


class StudentExamSubmitError(Exception):
    def __init__(self, detail: str, status_code: int, code: str = "online_submission_rejected"):
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code
        self.code = code


def _missing_exam_enrollment(reason: str) -> tuple[None, None]:
    logger.debug("student exam enrollment lookup missed: %s", reason)
    return MISSING_EXAM_ENROLLMENT


def student_exam_queryset(student, tenant, *, include_upcoming_days: int = 0, include_closed: bool = False):
    from django.db.models import Exists, OuterRef

    from apps.domains.attendance.models import Attendance
    from apps.domains.enrollment.selectors import active_enrollments_for_student
    from apps.domains.enrollment.models import SessionEnrollment
    from apps.domains.exams.models import Exam
    from apps.domains.lectures.models import Session
    from apps.domains.results.models import ExamAttempt, Result
    from apps.domains.submissions.models import Submission

    now = timezone.now()
    latest_open_at = now
    if include_upcoming_days > 0:
        latest_open_at = now + timedelta(days=include_upcoming_days)
    enrollments = active_enrollments_for_student(tenant=tenant, student=student)
    if include_upcoming_days > 0:
        enrollments = enrollments.filter(ongoing_lecture_filter(today=timezone.localdate(now)))
    enrollment_ids = list(enrollments.values_list("id", flat=True))
    if not enrollment_ids:
        return Exam.objects.none()
    absent_session_ids = Attendance.objects.filter(
        tenant=tenant,
        enrollment_id__in=enrollment_ids,
        status="ABSENT",
    ).values("session_id")
    # A different lecture's roster cannot make an unselected exam target actionable.
    eligible_session = (
        SessionEnrollment.objects.filter(
            tenant=tenant,
            enrollment_id__in=enrollment_ids,
            enrollment__tenant=tenant,
            enrollment__status="ACTIVE",
            enrollment__student__deleted_at__isnull=True,
            enrollment__lecture_id=F("session__lecture_id"),
            enrollment__exam_enrollments__exam_id=OuterRef("pk"),
            session__lecture__tenant=tenant,
            session__exams__id=OuterRef("pk"),
        )
        .exclude(session_id__in=absent_session_ids)
    )
    historical_result = Result.objects.filter(
        target_type="exam",
        target_id=OuterRef("pk"),
        enrollment_id__in=enrollment_ids,
        enrollment__tenant=tenant,
    )
    historical_attempt = ExamAttempt.objects.filter(
        exam_id=OuterRef("pk"),
        enrollment_id__in=enrollment_ids,
        enrollment__tenant=tenant,
    )
    recorded_result = historical_result.filter(
        Q(attempt__isnull=True)
        | Q(attempt__meta__status__isnull=True)
        | ~Q(attempt__meta__status="NOT_SUBMITTED")
    )
    historical_submission = Submission.objects.filter(
        tenant=tenant,
        target_type=Submission.TargetType.EXAM,
        target_id=OuterRef("pk"),
        enrollment_id__in=enrollment_ids,
    )
    absent_linked_attendance = Attendance.objects.filter(
        tenant=tenant,
        enrollment_id__in=enrollment_ids,
        enrollment__tenant=tenant,
        enrollment__status="ACTIVE",
        enrollment__student__deleted_at__isnull=True,
        enrollment__lecture_id=F("session__lecture_id"),
        enrollment__exam_enrollments__exam_id=OuterRef("pk"),
        session__lecture__tenant=tenant,
        session__exams__id=OuterRef("pk"),
        status="ABSENT",
    )
    return (
        Exam.objects.filter(
            exam_type=Exam.ExamType.REGULAR,
            exam_enrollments__enrollment_id__in=enrollment_ids,
            exam_enrollments__enrollment__lecture__is_active=True,
            sessions__lecture_id=F("exam_enrollments__enrollment__lecture_id"),
            sessions__lecture__tenant=tenant,
            is_active=True,
        )
        .filter(
            Q(open_at__isnull=True) | Q(open_at__lte=latest_open_at),
            Q() if include_closed else Q(close_at__isnull=True) | Q(close_at__gte=now),
        )
        .annotate(
            student_session_id=Subquery(Session.objects.filter(
                exams__id=OuterRef("pk"),
                lecture__tenant=tenant,
                lecture__enrollments__id__in=enrollment_ids,
                lecture__enrollments__exam_enrollments__exam_id=OuterRef("pk"),
            ).order_by("date", "order", "id").values("id")[:1]),
            learning_todo_eligible=Exists(eligible_session),
            has_historical_result=Exists(historical_result),
            recorded_result_attempt_count=Subquery(recorded_result.annotate(
                recorded_attempt=Coalesce("attempt__attempt_index", Value(1)),
            ).order_by("-recorded_attempt").values("recorded_attempt")[:1]),
            has_historical_attempt=Exists(historical_attempt),
            has_historical_submission=Exists(historical_submission),
            has_absent_linked_attendance=Exists(absent_linked_attendance),
        )
        .filter(
            Q(learning_todo_eligible=True)
            | Q(has_absent_linked_attendance=False)
            | Q(has_historical_result=True)
            | Q(has_historical_attempt=True)
            | Q(has_historical_submission=True)
        )
        .distinct()
        .order_by("open_at", "id")
    )


def submission_status_map_for_student_exams(*, tenant, student, exams, current_courses_only=False) -> dict[int, dict[str, int | bool]]:
    exam_ids = [int(exam.id) for exam in exams]
    if not exam_ids:
        return {}

    from apps.domains.enrollment.selectors import active_enrollments_for_student
    from apps.domains.submissions.models.submission import Submission

    enrollments = active_enrollments_for_student(tenant=tenant, student=student)
    if current_courses_only:
        enrollments = enrollments.filter(ongoing_lecture_filter(today=timezone.localdate()))
    enrollment_ids = list(enrollments.values_list("id", flat=True))
    if not enrollment_ids:
        return {}

    submission_status_map: dict[int, dict[str, int | bool]] = {}
    subs = Submission.objects.filter(
        tenant=tenant,
        enrollment_id__in=enrollment_ids,
        target_type=Submission.TargetType.EXAM,
        target_id__in=exam_ids,
    ).values_list("target_id", "status")
    for target_id, sub_status in subs:
        entry = submission_status_map.setdefault(
            int(target_id),
            {"has_result": False, "attempt_count": 0, "submission_pending": False},
        )
        if sub_status == Submission.Status.DONE:
            entry["has_result"] = True
            # Legacy completed submissions may predate canonical attempts.
            entry["attempt_count"] = max(int(entry["attempt_count"]), 1)
        if sub_status in {
            Submission.Status.SUBMITTED,
            Submission.Status.DISPATCHED,
            Submission.Status.EXTRACTING,
            Submission.Status.ANSWERS_READY,
            Submission.Status.GRADING,
        }:
            entry["submission_pending"] = True
    from apps.domains.results.models import ExamAttempt

    for row in ExamAttempt.objects.filter(
        exam_id__in=exam_ids,
        enrollment_id__in=enrollment_ids,
        enrollment__tenant=tenant,
    ).filter(
        Q(meta__status__isnull=True) | ~Q(meta__status="NOT_SUBMITTED"),
    ).values("exam_id").annotate(attempt_count=Max("attempt_index")):
        entry = submission_status_map.setdefault(
            int(row["exam_id"]),
            {"has_result": False, "attempt_count": 0, "submission_pending": False},
        )
        entry["attempt_count"] = max(int(entry["attempt_count"]), int(row["attempt_count"]))
    return submission_status_map


def student_exam_questions(exam):
    from apps.domains.exams.models import AnswerKey, ExamQuestion
    from apps.domains.exams.services.template_resolver import resolve_template_exam
    from apps.support.exams.numeric_short_answer import (
        NUMERIC_SHORT_ANSWER_FORMAT,
        math_numeric_short_answer_question_ids,
    )
    from apps.support.omr.score_shape import get_exam_score_shape

    template = resolve_template_exam(exam)
    questions = list(
        ExamQuestion.objects.filter(sheet__exam=template)
        .order_by("number")
        .values("id", "number", "score")
    )
    answer_key = AnswerKey.objects.filter(exam=template).only("answers").first()
    score_shape = get_exam_score_shape(exam)
    numeric_question_ids = math_numeric_short_answer_question_ids(
        subject=getattr(template, "subject", None),
        exam=exam,
        question_ids=(int(question["id"]) for question in questions),
        question_kind=score_shape.question_kind,
        answers=getattr(answer_key, "answers", None),
    )
    for question in questions:
        question["question_kind"] = score_shape.question_kind(int(question["id"]))
        question["answer_format"] = (
            NUMERIC_SHORT_ANSWER_FORMAT
            if int(question["id"]) in numeric_question_ids
            else "text"
        )
    return questions


def normalize_student_exam_answers(*, exam, answers) -> list[dict[str, int | str]]:
    from apps.support.exams.numeric_short_answer import (
        NUMERIC_SHORT_ANSWER_FORMAT,
        normalize_numeric_short_answer,
    )

    if isinstance(answers, dict):
        raw_answers = [
            {"exam_question_id": key, "answer": value}
            for key, value in answers.items()
        ]
    elif isinstance(answers, list):
        raw_answers = answers
    else:
        raise StudentExamSubmitError("answers 필드가 필요합니다 (리스트 또는 객체).", 400)

    question_specs = student_exam_questions(exam)
    question_by_id = {int(question["id"]): question for question in question_specs}
    normalized_answers: list[dict[str, int | str]] = []
    seen_question_ids: set[int] = set()
    for raw_answer in raw_answers:
        if not isinstance(raw_answer, dict):
            raise StudentExamSubmitError("답안 형식이 올바르지 않습니다.", 400)
        try:
            question_id = int(raw_answer.get("exam_question_id"))
        except (TypeError, ValueError):
            raise StudentExamSubmitError("문항 번호가 올바르지 않습니다.", 400)
        question = question_by_id.get(question_id)
        if question is None:
            raise StudentExamSubmitError("이 시험에 없는 문항은 제출할 수 없습니다.", 400)
        if question_id in seen_question_ids:
            raise StudentExamSubmitError("같은 문항의 답안을 중복 제출할 수 없습니다.", 400)
        seen_question_ids.add(question_id)

        answer = str(raw_answer.get("answer", "")).strip()
        if question["question_kind"] == "essay" and len(answer) > 4000:
            raise StudentExamSubmitError(
                f"{question['number']}번 서술형 답은 4,000자 이내로 입력해 주세요.", 400,
            )
        if question["answer_format"] == NUMERIC_SHORT_ANSWER_FORMAT:
            answer = normalize_numeric_short_answer(answer)
            if answer is None:
                raise StudentExamSubmitError(
                    f"{question['number']}번 답은 0~999 사이의 정수로 입력해 주세요.",
                    400,
                )
        normalized_answers.append({
            "exam_question_id": question_id,
            "answer": answer,
        })

    if not normalized_answers:
        raise StudentExamSubmitError("최소 1개 문항의 답을 입력하세요.", 400)
    return normalized_answers


def get_enrollment_for_student_exam(student, exam_id, tenant=None):
    from apps.domains.exams.models import ExamEnrollment

    if not student:
        return _missing_exam_enrollment("student is required")
    if not tenant:
        return _missing_exam_enrollment("tenant is required")
    if getattr(student, "tenant_id", None) != tenant.id:
        return _missing_exam_enrollment("student tenant mismatch")
    exam_enrollments = (
        ExamEnrollment.objects.filter(
            exam_id=int(exam_id),
            enrollment__student=student,
            enrollment__tenant=tenant,
            enrollment__status="ACTIVE",
            enrollment__lecture__is_active=True,
            exam__sessions__lecture_id=F("enrollment__lecture_id"),
            exam__sessions__lecture__tenant=tenant,
        )
        .select_related("enrollment", "enrollment__tenant")
        .order_by("id")
    )
    from apps.support.attendance.learning_todo_eligibility import (
        exam_is_learning_todo_eligible,
    )

    for exam_enrollment in exam_enrollments:
        if not exam_enrollment.enrollment:
            continue
        if exam_is_learning_todo_eligible(
            tenant_id=int(tenant.id),
            enrollment_id=int(exam_enrollment.enrollment_id),
            exam_id=int(exam_id),
        ):
            return (
                exam_enrollment.enrollment,
                getattr(exam_enrollment.enrollment, "tenant", None),
            )
    return _missing_exam_enrollment("eligible active exam enrollment not found")


def create_online_exam_submission(
    *,
    request_user,
    request_student,
    tenant,
    exam,
    enrollment,
    answers,
    client_request_id=None,
    expected_attempt_count=None,
):
    from django.db import IntegrityError, transaction

    from apps.domains.exams.models import ExamEnrollment
    from apps.domains.results.models import ExamAttempt
    from apps.domains.submissions.models import Submission
    from apps.domains.submissions.services.lifecycle import (
        IN_PROGRESS_STATUSES,
        fail_submission,
        retry_failed_submission,
    )
    from apps.support.results.grading_dependencies import (
        lock_exam_and_score_edit_scope_for_grading,
    )

    if client_request_id is not None:
        try:
            client_request_id = uuid.UUID(str(client_request_id))
        except (ValueError, TypeError, AttributeError) as exc:
            raise StudentExamSubmitError("제출 식별자가 올바르지 않습니다.", 400) from exc
    answers = sorted(answers, key=lambda row: row["exam_question_id"])
    if expected_attempt_count is not None and (
        type(expected_attempt_count) is not int or expected_attempt_count < 0
    ):
        raise StudentExamSubmitError("응시 상태가 올바르지 않습니다.", 400)
    try:
        with transaction.atomic():
            # Share grading's outer lock before touching attempts/submissions.
            # A duplicate retake must not hold the prior attempt while waiting
            # for the new submission that is currently grading.
            lock_exam_and_score_edit_scope_for_grading(
                exam_id=int(exam.id), tenant_id=int(tenant.id),
            )
            assignment = ExamEnrollment.objects.select_for_update().only("id").filter(
                exam_id=int(exam.id),
                enrollment_id=enrollment.id,
            ).first()
            if not assignment:
                raise StudentExamSubmitError("해당 시험에 응시할 수 없습니다.", 403)
            existing = None
            if client_request_id is not None:
                existing = Submission.objects.select_for_update().filter(
                    tenant=tenant, enrollment=enrollment,
                    target_type=Submission.TargetType.EXAM, target_id=exam.id,
                    source=Submission.Source.ONLINE, client_request_id=client_request_id,
                ).first()
                if existing:
                    if (existing.payload or {}).get("answers") != answers:
                        raise StudentExamSubmitError(
                            "이 제출의 답안이 변경되었습니다. 저장된 제출을 먼저 확인해 주세요.", 409,
                            "online_request_conflict",
                        )
                    if existing.status in (Submission.Status.DONE, Submission.Status.SUPERSEDED):
                        return existing
                    if existing.status == Submission.Status.SUBMITTED:
                        return existing
                    if existing.status != Submission.Status.FAILED:
                        raise StudentExamSubmitError("답안을 처리 중입니다. 잠시 후 다시 확인해 주세요.", 409,
                                                     "online_submission_processing")
                    if ExamAttempt.objects.filter(submission_id=existing.id).exists():
                        raise StudentExamSubmitError("이미 채점된 답안입니다. 결과를 확인해 주세요.", 409)

            # Read current policy after the same lock used by teacher edits.
            exam.refresh_from_db()
            validate_online_exam_readiness(exam)
            now = timezone.now()
            if exam.open_at and now < exam.open_at:
                raise StudentExamSubmitError("아직 시험 응시 시간이 아닙니다.", 409)
            if exam.close_at and now > exam.close_at:
                raise StudentExamSubmitError("시험이 마감되었습니다.", 409)
            attempt_indexes = list(
                ExamAttempt.objects.select_for_update()
                .filter(exam_id=int(exam.id), enrollment_id=enrollment.id)
                .values_list("attempt_index", flat=True)
            )
            prev_submissions = list(
                Submission.objects.select_for_update().filter(
                    tenant=tenant,
                    enrollment_id=enrollment.id,
                    target_type=Submission.TargetType.EXAM,
                    target_id=int(exam.id),
                    status__in=[*IN_PROGRESS_STATUSES, Submission.Status.DONE],
                )
            )
            # Older synchronous dispatch failures rolled back to SUBMITTED.
            # An explicit retry may retire only this student's stale, ungraded
            # ONLINE record, preserving its payload and every scored attempt.
            stale_before = timezone.now() - timedelta(minutes=30)
            for previous in prev_submissions:
                if (
                    previous.source == Submission.Source.ONLINE
                    and previous.status == Submission.Status.SUBMITTED
                    and previous.updated_at < stale_before
                    and not ExamAttempt.objects.filter(submission_id=previous.id).exists()
                ):
                    fail_submission(
                        previous,
                        error_message="이전 답안의 처리가 완료되지 않아 다시 제출했습니다.",
                        actor="student.exam_submit.stale_online_retry",
                    )
            in_progress = [s for s in prev_submissions if s.status in IN_PROGRESS_STATUSES]
            done_submissions = [s for s in prev_submissions if s.status == Submission.Status.DONE]
            attempt_count = max(attempt_indexes, default=len(done_submissions))

            if expected_attempt_count is not None and expected_attempt_count != attempt_count:
                raise StudentExamSubmitError(
                    "다른 화면에서 제출한 답안이 있습니다. 최신 결과를 확인해 주세요.", 409,
                    "online_attempt_changed",
                )

            if in_progress:
                raise StudentExamSubmitError("이미 제출된 시험입니다.", 409)

            if done_submissions or attempt_indexes:
                allow_retake = getattr(exam, "allow_retake", False)
                max_attempts = getattr(exam, "max_attempts", 1) or 1
                if not allow_retake or attempt_count >= max_attempts:
                    raise StudentExamSubmitError("재응시가 허용되지 않는 시험입니다.", 409)

            if existing:
                retry_failed_submission(existing, actor="student.exam_submit.request_retry")
                return existing

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
                client_request_id=client_request_id,
                meta=submission_meta,
                status=Submission.Status.SUBMITTED,
            )
    except IntegrityError as exc:
        if "unique_active_submission_per_target" in str(exc):
            raise StudentExamSubmitError("이미 제출된 시험입니다.", 409) from exc
        raise


def dispatch_student_exam_submission(submission) -> None:
    from django.db import transaction

    from apps.domains.submissions.models import Submission
    from apps.domains.submissions.services.dispatcher import dispatch_submission
    from apps.domains.submissions.services.lifecycle import supersede_done_submissions
    from apps.support.results.grading_dependencies import lock_exam_and_score_edit_scope_for_grading

    with transaction.atomic():
        lock_exam_and_score_edit_scope_for_grading(
            exam_id=int(submission.target_id), tenant_id=int(submission.tenant_id),
        )
        # An admitted duplicate may wait while the original and a later retake
        # finish. Only a newly processed submission may supersede prior results.
        submission.refresh_from_db(fields=["status"])
        if submission.status in (Submission.Status.DONE, Submission.Status.SUPERSEDED):
            return
        dispatch_submission(submission)
        submission.refresh_from_db(fields=["status"])
        if submission.status == Submission.Status.DONE:
            supersede_done_submissions(
                Submission.objects.filter(
                    tenant_id=submission.tenant_id,
                    enrollment_id=submission.enrollment_id,
                    target_type=submission.target_type,
                    target_id=submission.target_id,
                ).exclude(pk=submission.pk),
                actor="student.exam_submit.retake",
            )


def submit_online_exam(*, request_user, request_student, tenant, exam, enrollment,
                       answers, client_request_id=None, expected_attempt_count=None):
    """Shared admission, idempotent dispatch and durable failure for both APIs."""
    answers = normalize_student_exam_answers(exam=exam, answers=answers)
    submission = create_online_exam_submission(
        request_user=request_user, request_student=request_student, tenant=tenant,
        exam=exam, enrollment=enrollment, answers=answers, client_request_id=client_request_id,
        expected_attempt_count=expected_attempt_count,
    )
    try:
        dispatch_student_exam_submission(submission)
    except Exception as exc:
        logger.exception("Online exam submission dispatch failed: submission=%s", submission.id)
        fail_student_exam_submission(submission)
        raise StudentExamSubmitError(
            "제출 처리 중 오류가 발생했습니다. 입력한 답안으로 다시 제출해 주세요.", 500,
            "online_submission_retry",
        ) from exc
    submission.refresh_from_db(fields=["status"])
    return submission


def submit_online_exam_for_enrollment(*, request_user, tenant, exam_id, enrollment_id,
                                    answers, client_request_id=None, expected_attempt_count=None):
    """Legacy transport enters here only after its existing owner/role checks."""
    from apps.domains.enrollment.models import Enrollment
    from apps.domains.exams.models import Exam

    enrollment = Enrollment.objects.select_related("student__user").filter(
        id=enrollment_id, tenant=tenant, status="ACTIVE", student__deleted_at__isnull=True,
    ).first()
    if not enrollment:
        raise StudentExamSubmitError("해당 수강 정보에 접근할 수 없습니다.", 403)
    exam = Exam.objects.filter(id=exam_id, tenant=tenant).first()
    if not exam:
        raise StudentExamSubmitError("시험을 찾을 수 없습니다.", 404)
    return submit_online_exam(
        request_user=request_user, request_student=enrollment.student, tenant=tenant,
        exam=exam, enrollment=enrollment, answers=answers, client_request_id=client_request_id,
        expected_attempt_count=expected_attempt_count,
    )


def validate_online_exam_readiness(exam) -> None:
    """Missing automatic keys are setup errors, never student zero scores."""
    from django.core.exceptions import ValidationError

    from apps.domains.results.guards.grading_contract import GradingContractGuard

    try:
        GradingContractGuard.validate_exam_for_grading(exam, require_online_answers=True)
    except ValidationError as exc:
        raise StudentExamSubmitError(
            "시험의 문항·정답 준비가 완료되지 않았습니다. 선생님 확인 후 다시 제출해 주세요.",
            409,
        ) from exc


def fail_student_exam_submission(submission) -> None:
    from apps.support.submissions.failure import mark_submission_failed

    mark_submission_failed(
        int(submission.id),
        tenant_id=int(submission.tenant_id),
        error_message="답안 채점 처리에 실패했습니다. 다시 제출해 주세요.",
        actor="student.exam_submit.dispatch_failed",
    )
