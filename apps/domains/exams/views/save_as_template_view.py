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

from apps.core.permissions import TenantResolvedAndMember
from apps.domains.exams.models import Exam
from apps.domains.exams.serializers.exam import ExamSerializer
from apps.domains.results.permissions import IsTeacherOrAdmin


class SaveAsTemplateView(APIView):
    permission_classes = [IsAuthenticated, TenantResolvedAndMember, IsTeacherOrAdmin]

    def post(self, request, exam_id):
        exam = self._get_regular_exam(request, int(exam_id))
        if exam.template_exam_id is not None:
            raise ValidationError(
                {"detail": "This exam already has a template. Use that template or unlink first."}
            )

        template = Exam.objects.create(
            title=exam.title,
            description=exam.description or "",
            subject=exam.subject or "",
            exam_type=Exam.ExamType.TEMPLATE,
            template_exam=None,
            status=Exam.Status.DRAFT,
        )
        exam.template_exam = template
        exam.save(update_fields=["template_exam_id"])

        return Response(ExamSerializer(exam).data)

    def _get_regular_exam(self, request, exam_id):
        tenant = getattr(request, "tenant", None)
        if not tenant:
            raise PermissionDenied("Tenant required.")
        try:
            exam = Exam.objects.filter(
                sessions__lecture__tenant=tenant
            ).distinct().get(id=exam_id)
        except Exam.DoesNotExist:
            raise NotFound("Exam not found.")
        if exam.exam_type != Exam.ExamType.REGULAR:
            raise ValidationError(
                {"detail": "Only regular exams can be saved as template."}
            )
        return exam
