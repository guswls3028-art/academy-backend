from __future__ import annotations

from rest_framework.viewsets import ModelViewSet
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import ValidationError

from apps.core.permissions import TenantResolvedAndMember
from apps.domains.exams.models import AnswerKey, Exam
from apps.domains.exams.serializers.answer_key import AnswerKeySerializer

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
        qs = AnswerKey.objects.filter(
            exam__sessions__lecture__tenant=tenant
        ).select_related("exam").distinct()

        exam_id = self.request.query_params.get("exam")
        if not exam_id:
            return qs

        try:
            eid = int(exam_id)
        except (TypeError, ValueError):
            raise ValidationError({"exam": "must be integer"})

        exam = Exam.objects.filter(id=eid).first()
        if not exam:
            return qs.none()
        return qs.filter(exam_id=eid)

    def perform_create(self, serializer):
        exam: Exam = serializer.validated_data["exam"]
        serializer.save(exam=exam)

    def perform_update(self, serializer):
        serializer.save()

    def perform_destroy(self, instance):
        return super().perform_destroy(instance)
