# apps/domains/exams/services/template_resolver.py
from __future__ import annotations

from django.shortcuts import get_object_or_404
from rest_framework.exceptions import ValidationError

from apps.domains.exams.models import Exam


def resolve_template_exam(exam: Exam) -> Exam:
    """
    시험 단일진실 resolver

    - template → 자기 자신
    - regular → template_exam (있으면) / 없으면 자기 자신(템플릿 선택 전 단계)
    """
    if exam.exam_type == Exam.ExamType.TEMPLATE:
        return exam

    # regular은 template_exam이 있을 수도/없을 수도 있음 (템플릿 선택은 옵션)
    if not getattr(exam, "template_exam_id", None):
        return exam

    # select_related가 아닐 수 있으므로 안전하게 fetch
    return get_object_or_404(Exam, id=int(exam.template_exam_id), exam_type=Exam.ExamType.TEMPLATE)


def assert_template_editable(template_exam: Exam) -> None:
    """
    구조(Sheet/Question/AnswerKey/Asset)를 수정할 수 있는지 확인.

    정책(신규):
    - 템플릿 선택은 옵션
    - regular이 아직 template_exam이 없으면, 해당 regular 자체를 구조 단일진실로 간주하고 편집을 허용한다.
    - regular이 template_exam을 참조 중이면, 구조 편집은 template에서 수행해야 한다.
    """
    if template_exam.exam_type == Exam.ExamType.TEMPLATE:
        return

    if template_exam.exam_type == Exam.ExamType.REGULAR and template_exam.template_exam_id is None:
        return

    raise ValidationError({"detail": "template exam required"})
