from __future__ import annotations

from django.shortcuts import get_object_or_404
from django.db import transaction

from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.exceptions import PermissionDenied

from apps.domains.exams.models import Exam, Sheet, ExamQuestion
from apps.domains.exams.serializers.question import QuestionSerializer
from apps.domains.exams.serializers.question_init import ExamQuestionInitSerializer
from apps.domains.exams.services.template_resolver import resolve_template_exam, assert_template_editable
from apps.domains.results.permissions import IsTeacherOrAdmin


class ExamQuestionInitView(APIView):
    """
    POST /api/v1/exams/<exam_id>/questions/init/

    목적:
    - '문항선택하기'를 실제로 동작시키기 위한 최소 기능.
    - total_questions 만큼 1..N 문항을 생성/정리한다.
      - 기존 점수(score)는 유지 (새로 생성되는 문항에만 default_score 적용)
      - total_questions가 줄면 초과 문항 삭제

    정책:
    - template 시험 또는 (template 미지정 regular)만 구조 편집 가능
    - regular이 template을 참조 중이면 template에서 구조를 편집해야 함
    """

    permission_classes = [IsAuthenticated, IsTeacherOrAdmin]

    @transaction.atomic
    def post(self, request, exam_id: int):
        exam = get_object_or_404(Exam, id=int(exam_id))

        # regular이 template을 참조 중이면 여기서는 생성 금지(템플릿에서 편집)
        if exam.exam_type == Exam.ExamType.REGULAR and exam.template_exam_id is not None:
            raise PermissionDenied("This regular exam uses a template; edit questions on the template exam.")

        owner = resolve_template_exam(exam)
        assert_template_editable(owner)

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

