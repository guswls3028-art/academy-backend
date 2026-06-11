# apps/domains/exams/services/template_resolver.py
from __future__ import annotations

from django.shortcuts import get_object_or_404

try:
    from rest_framework.exceptions import ValidationError
except ImportError:
    from django.core.exceptions import ValidationError

from apps.domains.exams.models import Exam, Sheet


def resolve_structure_exam(exam: Exam) -> Exam:
    """
    시험 구조 단일진실 resolver

    - template → 자기 자신
    - regular with own Sheet → 자기 자신
    - legacy regular without own Sheet → template_exam
    - regular without template → 자기 자신
    """
    if exam.exam_type == Exam.ExamType.TEMPLATE:
        return exam

    if Sheet.objects.filter(exam_id=int(exam.id)).exists():
        return exam

    if not getattr(exam, "template_exam_id", None):
        return exam

    return get_object_or_404(Exam, id=int(exam.template_exam_id), exam_type=Exam.ExamType.TEMPLATE)


def resolve_template_exam(exam: Exam) -> Exam:
    """Backward-compatible name. Returns the effective structure owner."""
    return resolve_structure_exam(exam)


def assert_template_editable(template_exam: Exam) -> None:
    """
    구조(Sheet/Question/AnswerKey/Asset)를 수정할 수 있는지 확인.

    정책(신규):
    - 템플릿 선택은 옵션
    - regular은 자기 구조 소유자로 간주하고 편집을 허용한다.
    - template은 아직 live-linked legacy regular가 있으면 봉인한다.
    """
    if template_exam.exam_type == Exam.ExamType.TEMPLATE:
        if template_exam.derived_exams.filter(sheet__isnull=True).exists():
            raise ValidationError({"detail": "이미 운영 시험에 사용된 템플릿은 수정할 수 없습니다."})
        return

    if template_exam.exam_type == Exam.ExamType.REGULAR:
        return

    raise ValidationError({"detail": "template exam required"})
