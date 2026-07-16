"""Exam-domain DB read helpers for cross-domain callers."""

from __future__ import annotations


def regular_active_exam_for_tenant(exam_id: int, tenant):
    from apps.domains.exams.models import Exam

    return (
        Exam.objects.filter(
            id=int(exam_id),
            tenant=tenant,
            exam_type=Exam.ExamType.REGULAR,
            is_active=True,
        )
        .order_by("id")
        .first()
    )


def active_regular_exam_count(tenant) -> int:
    from apps.domains.exams.models import Exam

    return Exam.objects.filter(
        tenant=tenant,
        is_active=True,
        exam_type=Exam.ExamType.REGULAR,
    ).count()


def exam_enrollment_ids_for_tenant_exam(exam_id: int, tenant):
    from apps.domains.exams.models import ExamEnrollment

    return ExamEnrollment.objects.filter(
        exam_id=int(exam_id),
        enrollment__tenant=tenant,
    ).values_list("enrollment_id", flat=True)


def exam_question_number_map(question_ids, *, exam_id: int, tenant) -> dict[int, int]:
    from apps.domains.exams.models.question import ExamQuestion

    if not question_ids:
        return {}
    return dict(
        ExamQuestion.objects.filter(
            id__in=question_ids,
            sheet__exam_id=int(exam_id),
            sheet__exam__tenant=tenant,
        )
        .order_by("id")
        .values_list("id", "number")
    )


def answer_key_answers_for_exam(exam_id: int, *, tenant) -> dict:
    from apps.domains.exams.models import AnswerKey

    answer_key = (
        AnswerKey.objects.filter(
            exam_id=int(exam_id),
            exam__tenant=tenant,
        )
        .order_by("-updated_at", "-id")
        .first()
    )
    return answer_key.answers if answer_key and answer_key.answers else {}


def exam_target_info_map(exam_ids, tenant=None) -> dict[int, dict]:
    from apps.domains.exams.models import Exam

    info: dict[int, dict] = {}
    if not exam_ids:
        return info

    queryset = Exam.objects.filter(id__in=exam_ids)
    if tenant is not None:
        queryset = queryset.filter(tenant=tenant)

    for exam in queryset.prefetch_related("sessions__lecture").order_by("id"):
        session = exam.sessions.order_by("id").first()
        info[exam.id] = {
            "target_title": exam.title,
            "lecture_id": session.lecture_id if session else None,
            "lecture_title": session.lecture.title if session and session.lecture else "",
            "session_id": session.id if session else None,
        }
    return info
