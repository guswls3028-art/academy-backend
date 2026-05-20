from __future__ import annotations

from rest_framework.exceptions import ValidationError

from apps.domains.exams.models import Exam, ExamEnrollment


def validate_exam_enrollment_assigned(exam: Exam, enrollment_id: int) -> None:
    """
    Manual score writes may create Result/Attempt rows, so they must be limited
    to the roster explicitly assigned to the exam.
    """
    if exam.exam_type == Exam.ExamType.TEMPLATE:
        raise ValidationError({"detail": "템플릿 시험에는 점수를 입력할 수 없습니다."})

    if not ExamEnrollment.objects.filter(
        exam_id=exam.id,
        enrollment_id=enrollment_id,
    ).exists():
        raise ValidationError(
            {"enrollment_id": "이 시험의 응시 대상 수강생만 점수를 입력할 수 있습니다."}
        )
