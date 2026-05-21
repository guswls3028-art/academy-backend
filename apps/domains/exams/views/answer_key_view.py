from __future__ import annotations

from rest_framework.viewsets import ModelViewSet
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import ValidationError

from apps.core.permissions import TenantResolvedAndMember
from apps.domains.exams.models import AnswerKey, Exam
from apps.domains.exams.serializers.answer_key import AnswerKeySerializer
from apps.domains.exams.services.template_resolver import (
    assert_template_editable,
    resolve_template_exam,
)

from apps.domains.results.permissions import IsTeacherOrAdmin


class AnswerKeyViewSet(ModelViewSet):
    serializer_class = AnswerKeySerializer

    def get_permissions(self):
        if self.action in {"list", "retrieve"}:
            return [IsAuthenticated(), TenantResolvedAndMember()]
        return [IsAuthenticated(), IsTeacherOrAdmin()]

    def get_queryset(self):
        tenant = getattr(self.request, "tenant", None)
        if not tenant:
            return AnswerKey.objects.none()
        qs = AnswerKey.objects.filter(exam__tenant=tenant).select_related("exam").distinct().order_by("id")

        exam_id = self.request.query_params.get("exam")
        if not exam_id:
            return qs

        try:
            eid = int(exam_id)
        except (TypeError, ValueError):
            raise ValidationError({"exam": "must be integer"})

        exam = Exam.objects.filter(id=eid, tenant=tenant).first()
        if not exam:
            return qs.none()
        template_exam = resolve_template_exam(exam)
        if template_exam.tenant_id != tenant.id:
            return qs.none()
        return qs.filter(exam_id=template_exam.id)

    def _validate_exam_scope(self, exam: Exam) -> None:
        tenant = getattr(self.request, "tenant", None)
        if not tenant or exam.tenant_id != tenant.id:
            raise ValidationError({"exam": "현재 테넌트의 시험만 사용할 수 있습니다."})
        assert_template_editable(exam)

    def perform_create(self, serializer):
        exam: Exam = serializer.validated_data["exam"]
        self._validate_exam_scope(exam)
        serializer.save(exam=exam)

    def perform_update(self, serializer):
        exam = serializer.validated_data.get("exam") or serializer.instance.exam
        self._validate_exam_scope(exam)
        serializer.save()

    def perform_destroy(self, instance):
        self._validate_exam_scope(instance.exam)
        return super().perform_destroy(instance)
