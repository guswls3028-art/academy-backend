from __future__ import annotations

from rest_framework.viewsets import ModelViewSet
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import ValidationError, PermissionDenied
from rest_framework.response import Response

from apps.domains.exams.models import Exam
from apps.domains.exams.serializers.exam import ExamSerializer
from apps.domains.exams.serializers.exam_create import ExamCreateSerializer
from apps.domains.exams.serializers.exam_update import ExamUpdateSerializer
from apps.domains.lectures.models import Session

from apps.domains.results.permissions import IsTeacherOrAdmin


class ExamViewSet(ModelViewSet):
    """
    Exam 생성/조회/수정/삭제 API (봉인)

    봉인 규칙:
    - create/update/delete는 Teacher/Admin만
    - template: subject 필수, session_id/template_exam_id 입력 금지
    - regular: template_exam_id + session_id 필수, subject는 template 기반으로 봉인
    - update/patch에서 exam_type/subject/template_exam 변경 시도는 즉시 400
    - template 삭제: derived regular 존재 시 금지
    """

    queryset = Exam.objects.all()
    permission_classes = [IsAuthenticated]

    def get_permissions(self):
        # list/retrieve는 로그인만
        if self.action in {"list", "retrieve"}:
            return [IsAuthenticated()]
        # 생성/수정/삭제는 Teacher/Admin
        return [IsAuthenticated(), IsTeacherOrAdmin()]

    def get_serializer_class(self):
        if self.action == "create":
            return ExamCreateSerializer
        if self.action in {"update", "partial_update"}:
            return ExamUpdateSerializer
        return ExamSerializer

    def _reject_immutable_fields_on_update(self, request):
        # DRF는 fields에 없는 값은 무시될 수 있음 → 봉인 목적상 "들어오면 즉시 거절"
        forbidden = {"exam_type", "subject", "template_exam", "template_exam_id"}
        incoming = set(request.data.keys())
        bad = sorted(list(incoming & forbidden))
        if bad:
            raise ValidationError({"detail": f"Immutable fields in update are forbidden: {bad}"})

    def perform_create(self, serializer):
        exam_type = serializer.validated_data.get("exam_type")

        # =========================
        # TEMPLATE CREATE
        # =========================
        if exam_type == Exam.ExamType.TEMPLATE:
            if self.request.data.get("session_id"):
                raise ValidationError({"session_id": "template exam must not receive session_id"})
            if self.request.data.get("template_exam_id"):
                raise ValidationError({"template_exam_id": "template exam must not receive template_exam_id"})

            serializer.save(exam_type=Exam.ExamType.TEMPLATE, template_exam=None)
            return

        # =========================
        # REGULAR CREATE
        # =========================
        template_exam_id = self.request.data.get("template_exam_id")
        if not template_exam_id:
            raise ValidationError({"template_exam_id": "required"})
        try:
            template_exam_id = int(template_exam_id)
        except (TypeError, ValueError):
            raise ValidationError({"template_exam_id": "must be integer"})

        try:
            template_exam = Exam.objects.get(id=template_exam_id)
        except Exam.DoesNotExist:
            raise ValidationError({"template_exam_id": "invalid"})
        if template_exam.exam_type != Exam.ExamType.TEMPLATE:
            raise ValidationError({"template_exam_id": "must be template exam"})

        session_id = self.request.data.get("session_id")
        if not session_id:
            raise ValidationError({"session_id": "required"})
        try:
            session_id = int(session_id)
        except (TypeError, ValueError):
            raise ValidationError({"session_id": "must be integer"})

        try:
            session = Session.objects.get(id=session_id)
        except Session.DoesNotExist:
            raise ValidationError({"session_id": "invalid"})

        exam = serializer.save(
            exam_type=Exam.ExamType.REGULAR,
            subject=template_exam.subject,
            template_exam=template_exam,
        )
        exam.sessions.add(session)

    def update(self, request, *args, **kwargs):
        self._reject_immutable_fields_on_update(request)
        return super().update(request, *args, **kwargs)

    def partial_update(self, request, *args, **kwargs):
        self._reject_immutable_fields_on_update(request)
        return super().partial_update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        obj: Exam = self.get_object()

        # template 삭제 봉인: derived regular 존재하면 금지
        if obj.exam_type == Exam.ExamType.TEMPLATE and obj.derived_exams.exists():
            raise PermissionDenied("This template is used by regular exams and cannot be deleted.")

        return super().destroy(request, *args, **kwargs)

    def get_queryset(self):
        qs = super().get_queryset()

        exam_type = self.request.query_params.get("exam_type")
        if exam_type:
            qs = qs.filter(exam_type=exam_type)

        session_id = self.request.query_params.get("session_id")
        if session_id:
            try:
                sid = int(session_id)
            except (TypeError, ValueError):
                raise ValidationError({"session_id": "must be integer"})
            qs = qs.filter(sessions__id=sid)

        lecture_id = self.request.query_params.get("lecture_id")
        if lecture_id:
            try:
                lid = int(lecture_id)
            except (TypeError, ValueError):
                raise ValidationError({"lecture_id": "must be integer"})
            qs = qs.filter(sessions__lecture_id=lid)

        return qs.distinct().order_by("-created_at")
