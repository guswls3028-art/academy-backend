from __future__ import annotations

from django.http import Http404

from rest_framework.viewsets import ModelViewSet
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.core.permissions import TenantResolvedAndMember
from apps.domains.exams.models import AnswerKey, Exam
from apps.domains.exams.serializers.answer_key import AnswerKeySerializer
from apps.domains.exams.services.template_resolver import resolve_template_exam, assert_template_editable

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
        try:
            template = resolve_template_exam(exam)
        except (Http404, ValidationError):
            return qs.none()
        return qs.filter(exam=template)

    def perform_create(self, serializer):
        exam: Exam = serializer.validated_data["exam"]
        owner = resolve_template_exam(exam)

        # regular이 template을 참조 중이면 template에서 편집해야 함
        if exam.exam_type == Exam.ExamType.REGULAR and exam.template_exam_id is not None:
            raise PermissionDenied("This regular exam uses a template; edit answer key on the template exam.")

        # template 또는 (template 미지정 regular)만 편집 가능
        assert_template_editable(owner)
        serializer.save(exam=owner)

    def perform_update(self, serializer):
        obj: AnswerKey = self.get_object()
        assert_template_editable(obj.exam)
        serializer.save()

    def perform_destroy(self, instance):
        assert_template_editable(instance.exam)
        return super().perform_destroy(instance)
