from __future__ import annotations

from rest_framework.viewsets import ModelViewSet
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import ValidationError

from apps.core.permissions import TenantResolvedAndStaff
from apps.domains.exams.models import ExamQuestion, Sheet
from apps.domains.exams.serializers.question import (
    QuestionCreateSerializer,
    QuestionSerializer,
)
from apps.domains.exams.services.template_resolver import assert_template_editable

class QuestionViewSet(ModelViewSet):
    serializer_class = QuestionSerializer

    def get_serializer_class(self):
        if self.action == "create":
            return QuestionCreateSerializer
        return QuestionSerializer

    def get_permissions(self):
        return [IsAuthenticated(), TenantResolvedAndStaff()]

    def get_queryset(self):
        tenant = getattr(self.request, "tenant", None)
        if not tenant:
            return ExamQuestion.objects.none()
        # explanation은 reverse OneToOne — select_related로 N+1 회피
        # (QuestionSerializer.get_explanation_text/source가 obj.explanation 접근).
        return ExamQuestion.objects.filter(
            sheet__exam__tenant=tenant
        ).select_related("sheet", "sheet__exam", "explanation").distinct()

    def _assert_template_editable(self, obj: ExamQuestion):
        assert_template_editable(obj.sheet.exam)

    def _tenant_sheet(self, sheet_id: object) -> Sheet:
        tenant = getattr(self.request, "tenant", None)
        if not tenant:
            raise ValidationError({"sheet": "invalid sheet id"})
        try:
            return Sheet.objects.select_related("exam").get(
                id=int(sheet_id),
                exam__tenant=tenant,
            )
        except (Sheet.DoesNotExist, TypeError, ValueError):
            raise ValidationError({"sheet": "invalid sheet id"})

    def perform_create(self, serializer):
        sheet_id = self.request.data.get("sheet")
        if not sheet_id:
            raise ValidationError({"sheet": "sheet is required"})
        sheet = self._tenant_sheet(sheet_id)
        assert_template_editable(sheet.exam)
        serializer.save(sheet=sheet)

    def perform_update(self, serializer):
        obj = self.get_object()
        self._assert_template_editable(obj)
        requested_sheet = self.request.data.get("sheet")
        if requested_sheet is not None:
            try:
                same_sheet = int(requested_sheet) == obj.sheet_id
            except (TypeError, ValueError):
                same_sheet = False
            if not same_sheet:
                raise ValidationError({"sheet": "sheet cannot be changed"})
        serializer.save()

    def perform_destroy(self, instance):
        self._assert_template_editable(instance)
        return super().perform_destroy(instance)
