"""Cross-domain dependencies for wrong-note PDF views."""

from __future__ import annotations

from typing import Any

from django.utils import timezone


def get_wrong_note_pdf_enrollment(
    *,
    enrollment_id: int,
    tenant: Any,
    for_update: bool = False,
) -> Any | None:
    from apps.domains.enrollment.models import Enrollment

    queryset = (
        Enrollment.objects
        .filter(id=enrollment_id, tenant=tenant)
        .select_related("student", "lecture")
    )
    if for_update:
        queryset = queryset.select_for_update()
    return queryset.first()


def lecture_exists_for_tenant(*, lecture_id: int, tenant: Any) -> bool:
    from apps.domains.lectures.models import Lecture

    return Lecture.objects.filter(id=lecture_id, tenant=tenant).exists()


def exam_exists_for_tenant(*, exam_id: int, tenant: Any) -> bool:
    from apps.domains.exams.models import Exam

    return Exam.objects.filter(id=exam_id, tenant=tenant).exists()


def exam_is_attached_to_lecture(*, exam_id: int, lecture_id: int) -> bool:
    from apps.domains.exams.models import Exam

    exam = Exam.objects.filter(id=exam_id).first()
    if exam is None:
        return False
    return exam.sessions.filter(lecture_id=lecture_id).exists()


def create_wrong_note_pdf_ai_job(*, pdf_job_id: int, tenant_id: int) -> Any:
    from apps.domains.ai.models import AIJobModel

    return AIJobModel.objects.create(
        job_id=f"wrong-note-pdf-{pdf_job_id}",
        job_type="wrong_note_pdf_generation",
        status="PENDING",
        tenant_id=str(tenant_id),
        source_domain="results_wrong_note_pdf",
        source_id=str(pdf_job_id),
        payload={"wrong_note_pdf_job_id": int(pdf_job_id)},
        tier="basic",
    )


def publish_wrong_note_pdf_ai_job(ai_job: Any) -> bool:
    from apps.domains.ai.queueing.publisher import publish_ai_job_sqs

    return publish_ai_job_sqs(ai_job)


def mark_wrong_note_pdf_ai_job_failed(*, ai_job_id: int, error_message: str) -> None:
    from apps.domains.ai.models import AIJobModel

    now = timezone.now()
    AIJobModel.objects.filter(id=ai_job_id).update(
        status="FAILED",
        error_message=error_message,
        last_error=error_message,
        completed_at=now,
        updated_at=now,
    )


def get_wrong_note_pdf_ai_job_model():
    from apps.domains.ai.models import AIJobModel

    return AIJobModel
