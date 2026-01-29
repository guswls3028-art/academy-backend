from __future__ import annotations

from rest_framework.viewsets import ModelViewSet
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import PermissionDenied

from apps.domains.exams.models import ExamQuestion, Exam
from apps.domains.exams.serializers.question import QuestionSerializer
from apps.domains.exams.services.template_resolver import assert_template_editable

from apps.domains.results.permissions import IsTeacherOrAdmin


class QuestionViewSet(ModelViewSet):
    """
    ExamQuestion API (봉인)

    - list/retrieve: 로그인만
    - update/partial_update/destroy: Teacher/Admin + template exam만
    - template이 regular에 의해 사용 중이면 구조 변경 금지
    """

    queryset = ExamQuestion.objects.select_related("sheet", "sheet__exam")
    serializer_class = QuestionSerializer

    def get_permissions(self):
        if self.action in {"list", "retrieve"}:
            return [IsAuthenticated()]
        return [IsAuthenticated(), IsTeacherOrAdmin()]

    def _assert_template_editable(self, obj: ExamQuestion):
        if obj.sheet.exam.exam_type != Exam.ExamType.TEMPLATE:
            raise PermissionDenied("Questions can be modified only in template exams.")
        assert_template_editable(obj.sheet.exam)

    def perform_update(self, serializer):
        obj = self.get_object()
        self._assert_template_editable(obj)
        serializer.save()

    def perform_destroy(self, instance):
        self._assert_template_editable(instance)
        return super().perform_destroy(instance)
