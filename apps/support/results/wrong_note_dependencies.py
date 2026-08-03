"""Cross-domain read helpers for wrong-note result services."""

from __future__ import annotations

from typing import Any

from django.db.models import Prefetch


def regular_exam_ids_by_lecture_and_order(
    *,
    lecture_id: int,
    from_order: int,
    to_order: int | None,
) -> list[int]:
    from apps.domains.exams.models import Exam
    from apps.domains.lectures.models import Session

    session_filters = {
        "sessions__lecture_id": int(lecture_id),
        "sessions__session_type": Session.SessionType.REGULAR,
        "sessions__regular_order__gte": int(from_order),
    }
    if to_order is not None:
        session_filters["sessions__regular_order__lte"] = int(to_order)

    return list(
        Exam.objects.filter(
            exam_type=Exam.ExamType.REGULAR,
            **session_filters,
        )
        .values_list("id", flat=True)
        .distinct()
    )


def answer_key_map_for_effective_exam(
    *,
    exam_id: int,
    tenant_id: int,
) -> dict[str, Any]:
    from apps.domains.exams.models import AnswerKey, Exam

    exam = (
        Exam.objects
        .only("id", "exam_type", "template_exam_id")
        .filter(id=int(exam_id), tenant_id=int(tenant_id))
        .first()
    )
    if exam is None:
        return {}
    answer_key = AnswerKey.objects.filter(exam_id=exam.effective_template_exam_id).first()
    answers = getattr(answer_key, "answers", None) if answer_key else None
    return answers if isinstance(answers, dict) else {}


def exam_questions_by_id(
    *,
    question_ids: list[int],
    tenant_id: int,
) -> dict[int, Any]:
    from apps.domains.exams.models import ExamQuestion

    return (
        ExamQuestion.objects
        .filter(
            id__in=question_ids,
            sheet__exam__tenant_id=int(tenant_id),
        )
        .select_related("sheet", "explanation")
        .in_bulk(field_name="id")
    )


def exams_with_wrong_note_sessions_by_id(
    *,
    exam_ids: list[int],
    lecture_id: int | None,
    tenant_id: int,
    from_order: int | None = None,
    to_order: int | None = None,
) -> dict[int, Any]:
    from apps.domains.exams.models import Exam
    from apps.domains.lectures.models import Session

    sessions = Session.objects.filter(
        session_type=Session.SessionType.REGULAR,
    ).order_by("regular_order", "id")
    if lecture_id is not None:
        sessions = sessions.filter(lecture_id=int(lecture_id))
    if from_order is not None:
        sessions = sessions.filter(regular_order__gte=int(from_order))
    if to_order is not None:
        sessions = sessions.filter(regular_order__lte=int(to_order))

    return {
        int(exam.id): exam
        for exam in (
            Exam.objects
            .filter(id__in=exam_ids, tenant_id=int(tenant_id))
            .only("id", "title")
            .prefetch_related(
                Prefetch(
                    "sessions",
                    queryset=sessions.only(
                        "id",
                        "lecture_id",
                        "order",
                        "regular_order",
                        "session_type",
                        "title",
                    ),
                    to_attr="wrong_note_sessions",
                )
            )
        )
    }


def question_image_key(*, question: Any, tenant_id: int) -> str:
    key = str(getattr(question, "image_key", "") or "")
    return key if key.startswith(f"tenants/{int(tenant_id)}/") else ""


def question_image_url(*, question: Any, tenant_id: int) -> str:
    key = question_image_key(question=question, tenant_id=tenant_id)
    if key:
        from apps.infrastructure.storage.r2 import generate_presigned_get_url_storage

        try:
            return generate_presigned_get_url_storage(
                key=key,
                expires_in=3600,
            )
        except Exception:
            return ""

    image = getattr(question, "image", None)
    if not image:
        return ""
    try:
        return str(image.url or "")
    except Exception:
        return ""


def explanation_image_key(*, question: Any, tenant_id: int) -> str:
    try:
        key = str(question.explanation.image_key or "")
    except Exception:
        return ""
    return key if key.startswith(f"tenants/{int(tenant_id)}/") else ""


def explanation_image_url(*, question: Any, tenant_id: int) -> str:
    key = explanation_image_key(question=question, tenant_id=tenant_id)
    if not key:
        return ""
    from apps.infrastructure.storage.r2 import generate_presigned_get_url_storage

    try:
        return generate_presigned_get_url_storage(key=key, expires_in=3600)
    except Exception:
        return ""
