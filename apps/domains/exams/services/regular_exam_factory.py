# PATH: apps/domains/exams/services/regular_exam_factory.py
from __future__ import annotations

from typing import Optional

from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework.exceptions import ValidationError

from apps.domains.exams.models import Exam
from apps.domains.exams.services.template_builder_service import TemplateBuilderService
from apps.domains.exams.services.template_validation_service import TemplateValidationService
from apps.domains.lectures.models import Session


class RegularExamFactory:
    """
    ✅ PHASE 2-A
    템플릿(ExamType.TEMPLATE) 기반으로 실제 시험(regular)을 생성한다.

    원칙:
    - template은 SSOT, regular은 template_exam만 참조
    - template이 "regular 생성 가능한 상태"인지 서버에서 검증
    """

    @transaction.atomic
    def create_regular_from_template(
        self,
        *,
        template_exam: Exam,
        session_id: int,
        title: Optional[str] = None,
        description: Optional[str] = None,
    ) -> Exam:
        if template_exam.exam_type != Exam.ExamType.TEMPLATE:
            raise ValidationError({"detail": "template exam required"})

        # 1) 최소 구조 보장(시트/정답키 등)
        TemplateBuilderService.ensure_initialized(template_exam)

        # 2) regular 생성 가능 검증(questions/answerkey 등)
        valid = TemplateValidationService.validate_for_regular(template_exam)
        if not valid.get("ok"):
            raise ValidationError({"detail": f"template not ready: {valid.get('reason')}"})

        session = get_object_or_404(Session, id=int(session_id))

        # 3) regular 생성
        regular = Exam.objects.create(
            title=(title or template_exam.title).strip(),
            description=(description if description is not None else template_exam.description) or "",
            subject=template_exam.subject,
            exam_type=Exam.ExamType.REGULAR,
            template_exam=template_exam,
            is_active=True,
        )
        regular.sessions.add(session)

        return regular
