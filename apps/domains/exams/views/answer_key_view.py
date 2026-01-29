from __future__ import annotations

from django.shortcuts import get_object_or_404

from rest_framework.viewsets import ModelViewSet
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.domains.exams.models import AnswerKey, Exam
from apps.domains.exams.serializers.answer_key import AnswerKeySerializer
from apps.domains.exams.services.template_resolver import resolve_template_exam, assert_template_editable

from apps.domains.results.permissions import IsTeacherOrAdmin


class AnswerKeyViewSet(ModelViewSet):
    """
    AnswerKey API (봉인)

    - list/retrieve: 로그인만
      - ?exam=<id> 로 regular 접근 시 template로 resolve해서 단일진실 조회
    - create/update/destroy: Teacher/Admin + template only
    - template이 regular에 의해 사용 중이면 정답 변경 금지(운영 사고 차단)
    """

    queryset = AnswerKey.objects.select_related("exam")
    serializer_class = AnswerKeySerializer

    def get_permissions(self):
        if self.action in {"list", "retrieve"}:
            return [IsAuthenticated()]
        return [IsAuthenticated(), IsTeacherOrAdmin()]

    def get_queryset(self):
        qs = super().get_queryset()
        exam_id = self.request.query_params.get("exam")
        if not exam_id:
            return qs

        try:
            eid = int(exam_id)
        except (TypeError, ValueError):
            raise ValidationError({"exam": "must be integer"})

        exam = get_object_or_404(Exam, id=eid)
        template = resolve_template_exam(exam)
        return qs.filter(exam=template)

    def perform_create(self, serializer):
        exam: Exam = serializer.validated_data["exam"]
        if exam.exam_type != Exam.ExamType.TEMPLATE:
            raise PermissionDenied("AnswerKey can be created only for template exams.")
        assert_template_editable(exam)
        serializer.save()

    def perform_update(self, serializer):
        obj: AnswerKey = self.get_object()
        if obj.exam.exam_type != Exam.ExamType.TEMPLATE:
            raise PermissionDenied("AnswerKey can be updated only for template exams.")
        assert_template_editable(obj.exam)
        serializer.save()

    def perform_destroy(self, instance):
        if instance.exam.exam_type != Exam.ExamType.TEMPLATE:
            raise PermissionDenied("AnswerKey can be deleted only for template exams.")
        assert_template_editable(instance.exam)
        return super().perform_destroy(instance)
