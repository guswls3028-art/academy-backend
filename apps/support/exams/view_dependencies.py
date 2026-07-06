"""Cross-domain dependencies for exam views."""

from __future__ import annotations

from django.shortcuts import get_object_or_404

from apps.domains.results.permissions import IsTeacherOrAdmin

from apps.domains.assets.omr.services.meta_generator import MAX_MC_QUESTIONS


def get_session_model():
    from apps.domains.lectures.models import Session

    return Session


def get_session_for_tenant_or_404(*, session_id: int, tenant):
    from apps.domains.lectures.models import Session

    return get_object_or_404(Session, id=int(session_id), lecture__tenant=tenant)


def get_session_or_404(*, session_id: int):
    from apps.domains.lectures.models import Session

    return get_object_or_404(Session, id=int(session_id))


def build_omr_meta(**kwargs):
    from apps.domains.assets.omr.services.meta_generator import build_omr_meta as _build

    return _build(**kwargs)


def dispatch_ai_job(**kwargs):
    from apps.domains.ai.gateway import dispatch_job

    return dispatch_job(**kwargs)


def active_enrollment_ids_for_session(**kwargs) -> set[int]:
    from apps.domains.enrollment.selectors import active_enrollment_ids_for_session as _ids

    return _ids(**kwargs)


def active_session_enrollments_for_session(**kwargs):
    from apps.domains.enrollment.selectors import active_session_enrollments_for_session as _rows

    return _rows(**kwargs)


def pdf_extract_exam_validation_error(*, tenant, exam_id: int) -> str | None:
    from apps.domains.exams.models import Exam

    try:
        exam = Exam.objects.get(id=int(exam_id), tenant=tenant)
    except Exam.DoesNotExist:
        return "not_found"
    if exam.exam_type != Exam.ExamType.TEMPLATE:
        return "not_template"
    return None


def get_homework_template_for_bundle_item(*, homework_template_id: int, tenant):
    from apps.domains.homework_results.models.homework import Homework

    return Homework.objects.filter(
        pk=homework_template_id,
        tenant=tenant,
        homework_type=Homework.HomeworkType.TEMPLATE,
    ).first()


def find_session_for_bundle_apply(*, session_id: int, tenant):
    from apps.domains.lectures.models import Session

    try:
        return Session.objects.select_related("lecture").get(
            pk=session_id,
            lecture__tenant=tenant,
        )
    except Session.DoesNotExist:
        return None


def create_regular_homework_from_template(*, tenant, title: str, homework_template, session, config: dict):
    from apps.domains.homework_results.models.homework import Homework

    return Homework.objects.create(
        tenant=tenant,
        title=title,
        homework_type=Homework.HomeworkType.REGULAR,
        template_homework=homework_template,
        session=session,
        meta={"default_max_score": config.get("max_score", 100)},
    )


def active_session_enrollment_ids(session) -> list[int]:
    from apps.domains.enrollment.models import SessionEnrollment

    return list(
        SessionEnrollment.objects.filter(
            session=session,
            enrollment__status="ACTIVE",
        ).values_list("enrollment_id", flat=True)
    )


def bulk_create_homework_assignments(*, tenant, homework_id: int, session, enrollment_ids: list[int]) -> None:
    from apps.domains.homework.models.homework_assignment import HomeworkAssignment

    HomeworkAssignment.objects.bulk_create(
        [
            HomeworkAssignment(
                tenant=tenant,
                homework_id=homework_id,
                session=session,
                enrollment_id=enrollment_id,
            )
            for enrollment_id in enrollment_ids
        ],
        ignore_conflicts=True,
    )


def dispatch_progress_for_exam(*, exam_id: int) -> None:
    from apps.domains.progress.dispatcher import dispatch_progress_pipeline

    dispatch_progress_pipeline(exam_id=exam_id)


def regular_exam_delete_blocker(exam) -> str | None:
    from apps.domains.results.models import Result, ResultFact
    from apps.domains.submissions.models import Submission

    if exam.attempts.exists():
        return "exam attempts"
    if Submission.objects.filter(
        tenant=exam.tenant,
        target_type=Submission.TargetType.EXAM,
        target_id=exam.id,
    ).exists():
        return "submissions"
    if exam.results.exists():
        return "exam results"
    if Result.objects.filter(target_type="exam", target_id=exam.id).exists():
        return "results"
    if ResultFact.objects.filter(target_type="exam", target_id=exam.id).exists():
        return "result facts"
    return None


def resolve_removed_exam_clinic_links(
    *,
    tenant_id: int,
    session_id: int,
    exam_id: int,
    user_id: int | None,
) -> int:
    from apps.domains.progress.dispatcher import resolve_removed_source_clinic_links

    return resolve_removed_source_clinic_links(
        tenant_id=tenant_id,
        session_id=session_id,
        source_type="exam",
        source_id=exam_id,
        user_id=user_id,
        reason="exam_removed_from_session",
    )
