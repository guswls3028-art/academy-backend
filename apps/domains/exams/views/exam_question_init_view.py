from __future__ import annotations

from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.db import transaction
from django.utils import timezone

from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.core.permissions import TenantResolvedAndMember
from apps.domains.exams.models import Exam, Sheet, ExamQuestion
from apps.domains.exams.serializers.question import QuestionSerializer
from apps.domains.exams.serializers.question_init import ExamQuestionInitSerializer
from apps.domains.exams.services.template_resolver import (
    assert_template_editable,
    resolve_structure_exam,
)
from apps.domains.exams.services.structure_copy_service import ensure_regular_exam_owns_structure
from apps.support.exams.view_dependencies import IsTeacherOrAdmin


class ExamQuestionInitView(APIView):
    """
    POST /api/v1/exams/<exam_id>/questions/init/

    목적:
    - '문항선택하기'를 실제로 동작시키기 위한 최소 기능.
    - total_questions 만큼 1..N 문항을 생성/정리한다.
    - 템플릿은 선택: 요청한 exam_id 시험에 직접 문항을 생성/수정한다.
    """

    permission_classes = [IsAuthenticated, TenantResolvedAndMember, IsTeacherOrAdmin]

    @transaction.atomic
    def post(self, request, exam_id: int):
        tenant = request.tenant
        exam = get_object_or_404(
            Exam.objects.filter(
                Q(sessions__lecture__tenant=tenant)
                | Q(derived_exams__sessions__lecture__tenant=tenant)
                | Q(tenant=tenant)
            ).distinct(),
            id=int(exam_id),
        )

        ensure_regular_exam_owns_structure(exam)
        owner = resolve_structure_exam(exam)
        assert_template_editable(owner)

        s = ExamQuestionInitSerializer(data=request.data)
        s.is_valid(raise_exception=True)

        choice_count = s.validated_data.get("choice_count")
        choice_score_val = s.validated_data.get("choice_score")
        essay_count = s.validated_data.get("essay_count")
        essay_score_val = s.validated_data.get("essay_score")
        question_types = s.validated_data.get("question_types")

        if question_types is not None:
            choice_count = question_types.count("choice")
            essay_count = question_types.count("essay")

        use_choice_essay_counts = choice_count is not None and essay_count is not None
        use_choice_essay_scores = choice_score_val is not None and essay_score_val is not None

        if question_types is not None:
            total = len(question_types)
        elif use_choice_essay_counts:
            total = choice_count + essay_count
            if total == 0:
                total = 0
        else:
            total = int(s.validated_data["total_questions"])
        default_score = float(s.validated_data.get("default_score", 1.0))

        def score_for_number(n: int, existing_score: float | None = None) -> float:
            if use_choice_essay_scores:
                kind = question_types[n - 1] if question_types is not None else (
                    "choice" if n <= choice_count else "essay"
                )
                return float(choice_score_val) if kind == "choice" else float(essay_score_val)
            if existing_score is not None:
                return float(existing_score)
            return default_score

        sheet, _ = Sheet.objects.get_or_create(
            exam=owner,
            defaults={"name": "MAIN", "total_questions": total},
        )

        shape_updates = {
            "total_questions": total,
            "choice_count": int(choice_count) if choice_count is not None else total,
            "essay_count": int(essay_count) if essay_count is not None else 0,
        }
        if not use_choice_essay_counts:
            shape_updates["choice_count"] = total
            shape_updates["essay_count"] = 0

        changed_fields = [
            field
            for field, value in shape_updates.items()
            if getattr(sheet, field) != value
        ]
        if changed_fields:
            sheet.total_questions = total
            sheet.choice_count = shape_updates["choice_count"]
            sheet.essay_count = shape_updates["essay_count"]
            sheet.save(update_fields=[*changed_fields, "updated_at"])

        existing_numbers = set(
            ExamQuestion.objects.filter(sheet=sheet).values_list("number", flat=True)
        )
        new_numbers = set(range(1, total + 1))

        to_delete = existing_numbers - new_numbers
        if to_delete:
            ExamQuestion.objects.filter(sheet=sheet, number__in=to_delete).delete()

        to_create = sorted(list(new_numbers - existing_numbers))
        if to_create:
            ExamQuestion.objects.bulk_create(
                [
                    ExamQuestion(
                        sheet=sheet,
                        number=n,
                        score=score_for_number(n),
                        question_kind=question_types[n - 1] if question_types is not None else None,
                    )
                    for n in to_create
                ],
                ignore_conflicts=True,
            )

        existing_questions = ExamQuestion.objects.filter(sheet=sheet).order_by("number")
        kind_updates = []
        kind_updated_at = timezone.now()
        for question in existing_questions:
            next_kind = question_types[question.number - 1] if question_types is not None else None
            if question.question_kind != next_kind:
                question.question_kind = next_kind
                question.updated_at = kind_updated_at
                kind_updates.append(question)
        if kind_updates:
            ExamQuestion.objects.bulk_update(kind_updates, ["question_kind", "updated_at"])

        # 객관식/주관식 배점까지 명시된 경우에만 기존 문항 점수를 갱신한다.
        # count-only 적용은 선생님이 입력한 문항별 배점을 보존해야 한다.
        if use_choice_essay_scores:
            explicit_zero_reset = (
                float(choice_score_val or 0.0) == 0.0
                and float(essay_score_val or 0.0) == 0.0
            )
            has_existing_positive_score = ExamQuestion.objects.filter(
                sheet=sheet,
                score__gt=0,
            ).exists()
            should_update_existing_scores = not (
                explicit_zero_reset and has_existing_positive_score
            )
        else:
            should_update_existing_scores = False

        if should_update_existing_scores:
            for q in ExamQuestion.objects.filter(sheet=sheet):
                new_score = score_for_number(q.number, q.score)
                if q.score != new_score:
                    q.score = new_score
                    q.save(update_fields=["score", "updated_at"])

        qs = (
            ExamQuestion.objects.filter(sheet=sheet)
            .select_related("sheet", "explanation")
            .order_by("number")
        )
        return Response(QuestionSerializer(qs, many=True).data)
