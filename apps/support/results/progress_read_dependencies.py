"""Cross-domain read dependencies for result aggregations and clinic utilities."""

from __future__ import annotations

from typing import Any, Iterable


EXAM_SESSION_ORDERING = ("display_order", "created_at", "id")


def live_regular_exam_filter() -> dict[str, Any]:
    from apps.domains.exams.models import Exam

    return {
        "exam_type": Exam.ExamType.REGULAR,
        "is_active": True,
    }


def live_exams_for_session(session: Any):
    return (
        session.exams
        .filter(**live_regular_exam_filter())
        .distinct()
        .order_by(*EXAM_SESSION_ORDERING)
    )


def all_exams_for_session(session: Any):
    return session.exams.all().distinct().order_by(*EXAM_SESSION_ORDERING)


def live_exams_for_session_id(session_id: int):
    from apps.domains.exams.models import Exam

    return (
        Exam.objects
        .filter(sessions__id=int(session_id), **live_regular_exam_filter())
        .distinct()
        .order_by(*EXAM_SESSION_ORDERING)
    )


def live_sessions_for_exam(exam_id: int):
    from apps.domains.lectures.models import Session

    exam_filter = live_regular_exam_filter()
    return (
        Session.objects
        .filter(
            exams__id=int(exam_id),
            exams__exam_type=exam_filter["exam_type"],
            exams__is_active=exam_filter["is_active"],
        )
        .distinct()
        .order_by("order", "id")
    )


def sessions_for_global_snapshot(
    *,
    tenant_id: int,
    lecture_id: int | None,
    from_dt: Any | None,
    to_dt: Any | None,
):
    from apps.domains.lectures.models import Session

    sessions = Session.objects.filter(lecture__tenant_id=int(tenant_id))
    if lecture_id:
        sessions = sessions.filter(lecture_id=int(lecture_id))
    if from_dt or to_dt:
        if hasattr(Session, "updated_at"):
            if from_dt:
                sessions = sessions.filter(updated_at__gte=from_dt)
            if to_dt:
                sessions = sessions.filter(updated_at__lt=to_dt)
        elif hasattr(Session, "created_at"):
            if from_dt:
                sessions = sessions.filter(created_at__gte=from_dt)
            if to_dt:
                sessions = sessions.filter(created_at__lt=to_dt)
    return sessions


def sessions_by_ids(session_ids: Iterable[int]):
    from apps.domains.lectures.models import Session

    return Session.objects.filter(id__in=[int(session_id) for session_id in session_ids])


def lecture_by_id(lecture_id: int) -> Any | None:
    from apps.domains.lectures.models import Lecture

    return Lecture.objects.filter(id=int(lecture_id)).first()


def sessions_for_lecture(lecture: Any):
    from apps.domains.lectures.models import Session

    sessions_qs = Session.objects.filter(lecture=lecture).order_by("id")
    if hasattr(Session, "order"):
        try:
            return sessions_qs.order_by("order", "id")
        except Exception:
            return sessions_qs.order_by("id")
    return sessions_qs


def session_by_id(session_id: int) -> Any | None:
    from apps.domains.lectures.models import Session

    return Session.objects.filter(id=int(session_id)).select_related("lecture").first()


def progress_policy_meta_for_lecture(lecture: Any) -> dict[str, str]:
    from apps.domains.progress.models import ProgressPolicy

    try:
        policy = ProgressPolicy.objects.filter(lecture=lecture).first()
        return {
            "strategy": str(getattr(policy, "exam_aggregate_strategy", "MAX") or "MAX"),
            "pass_source": str(getattr(policy, "exam_pass_source", "EXAM") or "EXAM"),
        }
    except Exception:
        return {
            "strategy": "MAX",
            "pass_source": "EXAM",
        }


def session_progress_queryset_for_session(session: Any):
    from apps.domains.progress.models import SessionProgress

    return SessionProgress.objects.filter(session=session)


def session_progress_count_for_session_ids(session_ids: Iterable[int]) -> int:
    from apps.domains.progress.models import SessionProgress

    return SessionProgress.objects.filter(session_id__in=list(session_ids)).count()


def clinic_link_queryset_for_session(
    *,
    session: Any,
    tenant_id: int | None,
    include_manual: bool,
):
    from apps.domains.progress.models import ClinicLink

    qs = ClinicLink.objects.filter(session=session)
    if tenant_id is not None:
        qs = qs.filter(tenant_id=tenant_id)
    qs = qs.filter(resolved_at__isnull=True)
    if not include_manual:
        qs = qs.filter(is_auto=True)
    return qs.filter(enrollment__status="ACTIVE")


def unresolved_auto_clinic_links_for_enrollments(
    *,
    tenant: Any,
    enrollment_ids: set[int],
    session: Any | None,
):
    from apps.domains.progress.models import ClinicLink

    clinic_qs = ClinicLink.objects.filter(
        is_auto=True,
        resolved_at__isnull=True,
        enrollment_id__in=enrollment_ids,
        tenant=tenant,
    )
    if session is not None:
        clinic_qs = clinic_qs.filter(session=session)
    return clinic_qs


def completed_session_progress_pairs(*, session_ids: list[int], enrollment_ids: list[int]) -> set[tuple[int, int]]:
    from apps.domains.progress.models import SessionProgress

    return set(
        SessionProgress.objects.filter(
            session_id__in=session_ids,
            enrollment_id__in=enrollment_ids,
            completed=True,
        ).values_list("enrollment_id", "session_id")
    )


def completed_enrollment_ids_for_session(*, session: Any, enrollment_ids: set[int]) -> set[int]:
    from apps.domains.progress.models import SessionProgress

    return set(
        SessionProgress.objects.filter(
            session=session,
            enrollment_id__in=enrollment_ids,
            completed=True,
        ).values_list("enrollment_id", flat=True)
    )


def attended_clinic_enrollment_ids(*, tenant: Any, enrollment_ids: set[int]) -> set[int]:
    from apps.domains.clinic.models import SessionParticipant

    return set(
        SessionParticipant.objects.filter(
            tenant=tenant,
            enrollment_id__in=enrollment_ids,
            status=SessionParticipant.Status.ATTENDED,
        )
        .values_list("enrollment_id", flat=True)
        .distinct()
    )


def latest_exam_remediation_link(*, enrollment_id: int, exam_id: int, session: Any) -> Any | None:
    from apps.domains.progress.models import ClinicLink

    return (
        ClinicLink.objects.filter(
            enrollment_id=int(enrollment_id),
            session=session,
            source_type="exam",
            source_id=int(exam_id),
            resolved_at__isnull=False,
            resolution_type__in=[
                ClinicLink.ResolutionType.EXAM_PASS,
                ClinicLink.ResolutionType.MANUAL_OVERRIDE,
            ],
        )
        .order_by("-resolved_at")
        .first()
    )


def exam_remediation_link_values(
    *,
    enrollment_ids: set[int],
    exam_ids: set[int],
    session_ids: set[int],
    use_session_filter: bool,
) -> list[dict[str, Any]]:
    from apps.domains.progress.models import ClinicLink

    link_filter: dict[str, Any] = dict(
        enrollment_id__in=enrollment_ids,
        source_type="exam",
        source_id__in=exam_ids,
        resolved_at__isnull=False,
        resolution_type__in=[
            ClinicLink.ResolutionType.EXAM_PASS,
            ClinicLink.ResolutionType.MANUAL_OVERRIDE,
        ],
    )
    if use_session_filter and session_ids:
        link_filter["session_id__in"] = session_ids

    return list(
        ClinicLink.objects.filter(**link_filter).values(
            "enrollment_id",
            "source_id",
            "session_id",
            "resolution_type",
            "resolution_evidence",
            "resolved_at",
        )
    )
