from __future__ import annotations

from rest_framework.viewsets import ModelViewSet
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import PermissionDenied

from apps.core.permissions import TenantResolvedAndMember
from apps.domains.exams.models import ExamQuestion, Exam
from apps.domains.exams.serializers.question import QuestionSerializer
from apps.domains.exams.services.template_resolver import assert_template_editable

from apps.domains.results.permissions import IsTeacherOrAdmin


class QuestionViewSet(ModelViewSet):
    serializer_class = QuestionSerializer

    def get_permissions(self):
        if self.action in {"list", "retrieve"}:
            return [IsAuthenticated(), TenantResolvedAndMember()]
        return [IsAuthenticated(), IsTeacherOrAdmin()]

    def get_queryset(self):
        tenant = getattr(self.request, "tenant", None)
        if not tenant:
            return ExamQuestion.objects.none()
        return ExamQuestion.objects.filter(
            sheet__exam__sessions__lecture__tenant=tenant
        ).select_related("sheet", "sheet__exam").distinct()

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
