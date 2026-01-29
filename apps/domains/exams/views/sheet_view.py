from __future__ import annotations

from rest_framework.viewsets import ModelViewSet
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.domains.exams.models import Sheet, Exam
from apps.domains.exams.serializers.sheet import SheetSerializer
from apps.domains.exams.services.template_resolver import assert_template_editable

from apps.domains.results.permissions import IsTeacherOrAdmin


class SheetViewSet(ModelViewSet):
    queryset = Sheet.objects.select_related("exam")
    serializer_class = SheetSerializer

    def get_permissions(self):
        # 조회는 로그인만
        if self.action in {"list", "retrieve"}:
            return [IsAuthenticated()]
        # 생성/수정/삭제는 Teacher/Admin
        return [IsAuthenticated(), IsTeacherOrAdmin()]

    def _assert_exam_is_template(self, exam_id: int) -> Exam:
        try:
            exam = Exam.objects.get(id=int(exam_id))
        except Exam.DoesNotExist:
            raise ValidationError({"exam": "invalid exam id"})

        if exam.exam_type != Exam.ExamType.TEMPLATE:
            raise PermissionDenied("Sheet can be created/updated only for template exams.")

        # template이 regular에 의해 사용 중이면 구조 봉인
        assert_template_editable(exam)
        return exam

    def perform_create(self, serializer):
        exam_id = self.request.data.get("exam")
        if not exam_id:
            raise ValidationError({"exam": "exam is required"})
        exam = self._assert_exam_is_template(int(exam_id))

        # 1:1 강제
        if hasattr(exam, "sheet") and getattr(exam, "sheet", None) is not None:
            raise ValidationError({"exam": "This template exam already has a sheet (1:1)."})

        serializer.save(exam=exam)

    def perform_update(self, serializer):
        obj: Sheet = self.get_object()
        if obj.exam.exam_type != Exam.ExamType.TEMPLATE:
            raise PermissionDenied("Sheet can be updated only for template exams.")
        assert_template_editable(obj.exam)
        serializer.save()

    def perform_destroy(self, instance):
        if instance.exam.exam_type != Exam.ExamType.TEMPLATE:
            raise PermissionDenied("Sheet can be deleted only for template exams.")
        assert_template_editable(instance.exam)
        return super().perform_destroy(instance)
