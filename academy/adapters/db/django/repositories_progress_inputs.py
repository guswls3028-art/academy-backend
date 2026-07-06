"""DB read helpers for progress pipeline input resolution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SubmissionProgressTarget:
    enrollment_id: int
    target_type: str
    target_id: int


@dataclass(frozen=True)
class HomeworkPassScore:
    score: Any
    max_score: Any


def get_session_with_lecture(session_id: int) -> Any | None:
    from apps.domains.lectures.models import Session

    return (
        Session.objects.filter(id=int(session_id))
        .select_related("lecture")
        .first()
    )


def get_submission_progress_target_for_update(
    submission_id: int,
) -> SubmissionProgressTarget | None:
    from apps.domains.submissions.models import Submission

    submission = (
        Submission.objects.select_for_update()
        .filter(id=int(submission_id))
        .only("id", "enrollment_id", "target_type", "target_id")
        .first()
    )
    if not submission:
        return None

    enrollment_id = getattr(submission, "enrollment_id", None)
    target_type = str(getattr(submission, "target_type", "") or "")
    target_id = int(getattr(submission, "target_id", 0) or 0)
    if not enrollment_id or not target_type or not target_id:
        return SubmissionProgressTarget(
            enrollment_id=int(enrollment_id or 0),
            target_type=target_type,
            target_id=target_id,
        )
    return SubmissionProgressTarget(
        enrollment_id=int(enrollment_id),
        target_type=target_type,
        target_id=target_id,
    )


def list_enrollment_ids_with_exam_result(exam_id: int) -> list[int]:
    from apps.domains.results.models import Result

    enrollment_ids = (
        Result.objects.filter(target_type="exam", target_id=int(exam_id))
        .values_list("enrollment_id", flat=True)
        .distinct()
    )
    return [int(value) for value in enrollment_ids if value is not None]


def list_sessions_for_exam(exam_id: int) -> list[Any]:
    from apps.domains.lectures.models import Session

    try:
        from apps.domains.results.utils.session_exam import get_session_ids_for_exam

        session_ids = get_session_ids_for_exam(int(exam_id))
        if session_ids:
            return list(
                Session.objects.filter(id__in=[int(value) for value in session_ids])
                .select_related("lecture")
            )
    except Exception:
        pass

    try:
        return list(
            Session.objects.filter(exams__id=int(exam_id))
            .select_related("lecture")
            .distinct()
        )
    except Exception:
        return []


def list_sessions_for_homework(homework_id: int) -> list[Any]:
    from apps.domains.homework_results.models import Homework
    from apps.domains.lectures.models import Session

    homework = (
        Homework.objects.filter(id=int(homework_id))
        .only("id", "session_id")
        .first()
    )
    if not homework:
        return []

    session_ids: set[int] = set()
    if homework.session_id:
        session_ids.add(int(homework.session_id))

    try:
        sessions = getattr(homework, "sessions", None)
        if sessions is not None and hasattr(sessions, "values_list"):
            session_ids.update(int(value) for value in sessions.values_list("id", flat=True))
    except Exception:
        pass

    if not session_ids:
        return []
    return list(Session.objects.filter(id__in=session_ids).select_related("lecture"))


def get_representative_exam_attempt_id(exam_id: int, enrollment_id: int) -> int | None:
    from apps.domains.results.models import ExamAttempt

    attempt = (
        ExamAttempt.objects.filter(
            exam_id=int(exam_id),
            enrollment_id=int(enrollment_id),
            is_representative=True,
        )
        .order_by("-attempt_index")
        .only("id")
        .first()
    )
    return int(attempt.id) if attempt else None


def has_unresolved_clinic_link(enrollment_id: int, session_id: int) -> bool:
    from apps.domains.progress.models import ClinicLink

    return ClinicLink.objects.filter(
        enrollment_id=int(enrollment_id),
        session_id=int(session_id),
        resolved_at__isnull=True,
    ).exists()


def list_unresolved_homework_source_ids(enrollment_id: int, session_id: int) -> list[int]:
    from apps.domains.progress.models import ClinicLink

    source_ids = (
        ClinicLink.objects.filter(
            enrollment_id=int(enrollment_id),
            session_id=int(session_id),
            source_type="homework",
            resolved_at__isnull=True,
        )
        .values_list("source_id", flat=True)
        .distinct()
    )
    return [int(value) for value in source_ids if value is not None]


def get_passed_homework_score(
    *,
    enrollment_id: int,
    session_id: int,
    homework_id: int,
) -> HomeworkPassScore | None:
    from apps.domains.homework_results.models import HomeworkScore

    homework_score = (
        HomeworkScore.objects.filter(
            enrollment_id=int(enrollment_id),
            session_id=int(session_id),
            homework_id=int(homework_id),
            attempt_index=1,
            passed=True,
        )
        .only("score", "max_score")
        .first()
    )
    if not homework_score:
        return None
    return HomeworkPassScore(
        score=homework_score.score,
        max_score=homework_score.max_score,
    )
