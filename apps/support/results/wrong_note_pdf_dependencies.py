"""Cross-domain dependencies for wrong-note PDF views."""

from __future__ import annotations

from typing import Any


def get_wrong_note_pdf_enrollment(*, enrollment_id: int, tenant: Any) -> Any | None:
    from apps.domains.enrollment.models import Enrollment

    return (
        Enrollment.objects
        .filter(id=enrollment_id, tenant=tenant)
        .select_related("student", "lecture")
        .first()
    )


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
