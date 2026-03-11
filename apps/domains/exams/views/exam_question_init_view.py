from __future__ import annotations

from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.db import transaction

from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.domains.exams.models import Exam, Sheet, ExamQuestion
from apps.domains.exams.serializers.question import QuestionSerializer
from apps.domains.exams.serializers.question_init import ExamQuestionInitSerializer
from apps.domains.results.permissions import IsTeacherOrAdmin


class ExamQuestionInitView(APIView):
    """
    POST /api/v1/exams/<exam_id>/questions/init/

    목적:
    - '문항선택하기'를 실제로 동작시키기 위한 최소 기능.
    - total_questions 만큼 1..N 문항을 생성/정리한다.
    - 템플릿은 선택: 요청한 exam_id 시험에 직접 문항을 생성/수정한다.
    """

    permission_classes = [IsAuthenticated, IsTeacherOrAdmin]

    @transaction.atomic
    def post(self, request, exam_id: int):
        tenant = request.tenant
        exam = get_object_or_404(
            Exam.objects.filter(
                Q(sessions__lecture__tenant=tenant)
                | Q(derived_exams__sessions__lecture__tenant=tenant)
            ).distinct(),
            id=int(exam_id),
        )

        owner = exam

        s = ExamQuestionInitSerializer(data=request.data)
        s.is_valid(raise_exception=True)

        choice_count = s.validated_data.get("choice_count")
        choice_score_val = s.validated_data.get("choice_score")
        essay_count = s.validated_data.get("essay_count")
        essay_score_val = s.validated_data.get("essay_score")

        use_choice_essay = (
            choice_count is not None
            and choice_score_val is not None
            and essay_count is not None
            and essay_score_val is not None
        )

        if use_choice_essay:
            total = choice_count + essay_count
            if total == 0:
                total = 0
        else:
            total = int(s.validated_data["total_questions"])
        default_score = float(s.validated_data.get("default_score", 1.0))

        def score_for_number(n: int) -> float:
            if use_choice_essay:
                return float(choice_score_val) if n <= choice_count else float(essay_score_val)
            return default_score

        sheet, _ = Sheet.objects.get_or_create(
            exam=owner,
            defaults={"name": "MAIN", "total_questions": total},
        )

        if sheet.total_questions != total:
            sheet.total_questions = total
            sheet.save(update_fields=["total_questions", "updated_at"])

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
                    ExamQuestion(sheet=sheet, number=n, score=score_for_number(n))
                    for n in to_create
                ],
                ignore_conflicts=True,
            )

        # 객관식/주관식 모드: 기존 문항 점수도 번호에 맞게 갱신
        if use_choice_essay:
            for q in ExamQuestion.objects.filter(sheet=sheet):
                new_score = score_for_number(q.number)
                if q.score != new_score:
                    q.score = new_score
                    q.save(update_fields=["score", "updated_at"])

        qs = (
            ExamQuestion.objects.filter(sheet=sheet)
            .select_related("sheet")
            .order_by("number")
        )
        return Response(QuestionSerializer(qs, many=True).data)

