# apps/domains/exams/services/template_resolver.py
from __future__ import annotations

from django.shortcuts import get_object_or_404
from rest_framework.exceptions import ValidationError

from apps.domains.exams.models import Exam


def resolve_template_exam(exam: Exam) -> Exam:
    """
    시험 단일진실 resolver

    - template → 자기 자신
    - regular → template_exam (반드시 존재해야 함)
    """
    if exam.exam_type == Exam.ExamType.TEMPLATE:
        return exam

    # regular은 template_exam이 필수 (DB constraint + 여기서도 방어)
    if not getattr(exam, "template_exam_id", None):
        raise ValidationError({"detail": "regular exam must have template_exam"})

    # select_related가 아닐 수 있으므로 안전하게 fetch
    return get_object_or_404(Exam, id=int(exam.template_exam_id), exam_type=Exam.ExamType.TEMPLATE)


def assert_template_editable(template_exam: Exam) -> None:
    """
    template exam의 구조(Sheet/Question/AnswerKey/Asset)를 수정할 수 있는지 확인.

    봉인 규칙:
    - template이 이미 regular에 의해 참조(derived_exams 존재)되면
      구조 변경은 금지한다. (운영 사고 차단)
    """
    if template_exam.exam_type != Exam.ExamType.TEMPLATE:
        raise ValidationError({"detail": "template exam required"})

    if template_exam.derived_exams.exists():
        raise ValidationError(
            {"detail": "This template is already used by regular exams; structural edits are locked."}
        )
