# PATH: apps/domains/exams/views/save_as_template_view.py
"""
POST /api/v1/exams/<exam_id>/save-as-template/

- regular 시험이 template_exam 없을 때, 현재 설정을 바탕으로 템플릿을 생성해 연결합니다.
- 진행하기(OPEN) 전에 호출하면 "시험 설정 다 하고 open 시키면 템플릿으로 저장"을 만족합니다.
"""
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import ValidationError, NotFound, PermissionDenied

from django.db import transaction

from apps.core.permissions import TenantResolvedAndMember
from apps.domains.exams.models import AnswerKey, Exam, ExamAsset, ExamQuestion, Sheet
from apps.domains.exams.serializers.exam import ExamSerializer
from apps.support.exams.view_dependencies import IsTeacherOrAdmin


def _copy_sheet(source_exam: Exam, template_exam: Exam) -> dict[int, int]:
    try:
        source_sheet = source_exam.sheet
    except Sheet.DoesNotExist:
        return {}

    template_sheet = Sheet.objects.create(
        exam=template_exam,
        name=source_sheet.name,
        total_questions=source_sheet.total_questions,
        choice_count=source_sheet.choice_count,
        essay_count=source_sheet.essay_count,
        file=source_sheet.file,
    )

    question_id_map: dict[int, int] = {}
    for question in source_sheet.questions.order_by("number", "id"):
        copied_question = ExamQuestion.objects.create(
            sheet=template_sheet,
            number=question.number,
            score=question.score,
            image=question.image,
            image_key=question.image_key,
            region_meta=question.region_meta,
        )
        question_id_map[int(question.id)] = int(copied_question.id)
    return question_id_map


def _copy_answer_key(source_exam: Exam, template_exam: Exam, question_id_map: dict[int, int]) -> None:
    try:
        source_answer_key = source_exam.answer_key
    except AnswerKey.DoesNotExist:
        return

    answers = {}
    for key, value in (source_answer_key.answers or {}).items():
        try:
            mapped_key = str(question_id_map.get(int(key), int(key)))
        except (TypeError, ValueError):
            mapped_key = str(key)
        answers[mapped_key] = value

    AnswerKey.objects.create(exam=template_exam, answers=answers)


def _copy_assets(source_exam: Exam, template_exam: Exam) -> None:
    for asset in source_exam.assets.order_by("asset_type", "id"):
        ExamAsset.objects.create(
            exam=template_exam,
            asset_type=asset.asset_type,
            file_key=asset.file_key,
            file_type=asset.file_type,
            file_size=asset.file_size,
        )


class SaveAsTemplateView(APIView):
    permission_classes = [IsAuthenticated, TenantResolvedAndMember, IsTeacherOrAdmin]

    def post(self, request, exam_id):
        exam = self._get_regular_exam(request, int(exam_id))
        if exam.template_exam_id is not None:
            raise ValidationError(
                {"detail": "This exam already has a template. Use that template or unlink first."}
            )

        tenant = getattr(request, "tenant", None)
        title = str(request.data.get("title") or "").strip() or exam.title
        with transaction.atomic():
            template = Exam.objects.create(
                title=title,
                description=exam.description or "",
                subject=exam.subject or "",
                exam_type=Exam.ExamType.TEMPLATE,
                template_exam=None,
                tenant=tenant,
                is_active=exam.is_active,
                allow_retake=exam.allow_retake,
                max_attempts=exam.max_attempts,
                pass_score=exam.pass_score,
                max_score=exam.max_score,
                answer_visibility=exam.answer_visibility,
            )
            question_id_map = _copy_sheet(exam, template)
            _copy_answer_key(exam, template, question_id_map)
            _copy_assets(exam, template)

            exam.template_exam = template
            exam.save(update_fields=["template_exam_id"])

        return Response(ExamSerializer(exam).data)

    def _get_regular_exam(self, request, exam_id):
        tenant = getattr(request, "tenant", None)
        if not tenant:
            raise PermissionDenied("Tenant required.")
        try:
            exam = Exam.objects.filter(
                tenant=tenant,
                sessions__lecture__tenant=tenant,
            ).distinct().get(id=exam_id)
        except Exam.DoesNotExist:
            raise NotFound("Exam not found.")
        if exam.exam_type != Exam.ExamType.REGULAR:
            raise ValidationError(
                {"detail": "Only regular exams can be saved as template."}
            )
        return exam
