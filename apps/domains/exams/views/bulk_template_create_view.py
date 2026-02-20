# PATH: apps/domains/exams/views/bulk_template_create_view.py
"""
POST /exams/bulk-template/
한 번의 요청으로 템플릿 + 시트 + 문항 + 정답표까지 생성 (원테이크).
"""
from __future__ import annotations

from django.db import transaction
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from rest_framework.exceptions import ValidationError

from apps.domains.exams.models import Exam, ExamQuestion, AnswerKey, Sheet
from apps.domains.exams.services.template_builder_service import TemplateBuilderService
from apps.domains.results.permissions import IsTeacherOrAdmin


class BulkTemplateCreateView(APIView):
    """
    POST body: {
      "title": str,
      "subject": str,
      "description": str (optional),
      "questions": [ { "number": int, "score": float, "answer": str }, ... ]
    }
    응답: { "id": exam_id }
    """

    permission_classes = [IsAuthenticated, IsTeacherOrAdmin]

    def post(self, request):
        title = (request.data.get("title") or "").strip()
        subject = (request.data.get("subject") or "").strip()
        description = (request.data.get("description") or "").strip()
        questions = request.data.get("questions")

        if not title:
            raise ValidationError({"detail": "title 필수입니다."})
        if not isinstance(questions, list) or len(questions) == 0:
            raise ValidationError({"detail": "questions 배열을 1개 이상 입력하세요."})

        out = self._create_bulk(title=title, subject=subject, description=description, questions=questions)
        return Response(out, status=status.HTTP_201_CREATED)

    @staticmethod
    @transaction.atomic
    def _create_bulk(*, title: str, subject: str, description: str, questions: list) -> dict:
        exam = Exam.objects.create(
            title=title,
            subject=subject,
            description=description,
            exam_type=Exam.ExamType.TEMPLATE,
        )
        init = TemplateBuilderService.ensure_initialized(exam)
        sheet_id = init["sheet_id"]
        answer_key_id = init["answer_key_id"]
        sheet = Sheet.objects.get(id=sheet_id)
        answer_key = AnswerKey.objects.get(id=answer_key_id)

        question_ids = []
        for i, q in enumerate(questions):
            num = int(q.get("number") or (i + 1))
            sc = float(q.get("score") or 1)
            obj = ExamQuestion.objects.create(
                sheet_id=sheet_id,
                number=num,
                score=sc,
            )
            question_ids.append((obj.id, str(q.get("answer") or "").strip() or "1"))

        answers = {str(qid): ans for qid, ans in question_ids}
        answer_key.answers = answers
        answer_key.save(update_fields=["answers", "updated_at"])

        sheet.total_questions = len(question_ids)
        sheet.save(update_fields=["total_questions", "updated_at"])

        return {"id": exam.id}
