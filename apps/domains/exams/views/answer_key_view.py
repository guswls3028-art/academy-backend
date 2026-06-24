from __future__ import annotations

from rest_framework.viewsets import ModelViewSet
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import ValidationError

from apps.core.permissions import TenantResolvedAndMember
from apps.domains.exams.models import AnswerKey, Exam
from apps.domains.exams.serializers.answer_key import AnswerKeySerializer
from apps.domains.exams.services.template_resolver import (
    assert_template_editable,
    resolve_structure_exam,
)
from apps.domains.exams.services.structure_copy_service import (
    ensure_regular_exam_owns_structure,
    remap_answer_keys,
)

from apps.domains.results.permissions import IsTeacherOrAdmin


class AnswerKeyViewSet(ModelViewSet):
    serializer_class = AnswerKeySerializer

    def get_permissions(self):
        if self.action in {"list", "retrieve"}:
            return [IsAuthenticated(), TenantResolvedAndMember()]
        return [IsAuthenticated(), TenantResolvedAndMember(), IsTeacherOrAdmin()]

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
        owner_exam = resolve_structure_exam(exam)
        if owner_exam.tenant_id != tenant.id:
            return qs.none()
        return qs.filter(exam_id=owner_exam.id)

    def _validate_exam_scope(self, exam: Exam):
        tenant = getattr(self.request, "tenant", None)
        if not tenant or exam.tenant_id != tenant.id:
            raise ValidationError({"exam": "현재 테넌트의 시험만 사용할 수 있습니다."})
        copy_result = ensure_regular_exam_owns_structure(exam)
        owner_exam = resolve_structure_exam(exam)
        assert_template_editable(owner_exam)
        return owner_exam, copy_result

    def perform_create(self, serializer):
        exam: Exam = serializer.validated_data["exam"]
        owner_exam, copy_result = self._validate_exam_scope(exam)
        answers = serializer.validated_data["answers"]
        if copy_result.question_id_map:
            answers = remap_answer_keys(answers, copy_result.question_id_map)
        answer_key, _ = AnswerKey.objects.update_or_create(
            exam=owner_exam,
            defaults={"answers": answers},
        )
        serializer.instance = answer_key

    def perform_update(self, serializer):
        exam = serializer.validated_data.get("exam") or serializer.instance.exam
        owner_exam, copy_result = self._validate_exam_scope(exam)
        if (
            owner_exam.id != serializer.instance.exam_id
            and AnswerKey.objects.filter(exam=owner_exam)
            .exclude(pk=serializer.instance.pk)
            .exists()
        ):
            raise ValidationError({"exam": "answer key already exists for this exam"})
        answers = serializer.validated_data.get("answers")
        if answers is not None and copy_result.question_id_map:
            answers = remap_answer_keys(answers, copy_result.question_id_map)
            serializer.save(exam=owner_exam, answers=answers)
            return
        serializer.save(exam=owner_exam)

    def perform_destroy(self, instance):
        self._validate_exam_scope(instance.exam)
        return super().perform_destroy(instance)
