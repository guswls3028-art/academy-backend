from __future__ import annotations

from rest_framework.viewsets import ModelViewSet
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import ValidationError, PermissionDenied
from rest_framework.response import Response

from apps.core.permissions import TenantResolvedAndMember
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
    permission_classes = [IsAuthenticated, TenantResolvedAndMember]

    # ================================
    # Serializer 선택
    # ================================
    def get_serializer_class(self):
        if self.action == "create":
            return ExamCreateSerializer
        if self.action in {"update", "partial_update"}:
            return ExamUpdateSerializer
        return ExamSerializer

    # ================================
    # Permissions
    # ================================
    def get_permissions(self):
        if self.action in {"list", "retrieve"}:
            return [IsAuthenticated()]
        return [IsAuthenticated(), IsTeacherOrAdmin()]

    # ================================
    # 🔥 핵심 FIX: create 응답을 read serializer로
    # ================================
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        self.perform_create(serializer)
        instance = serializer.instance

        # ✅ 응답은 반드시 read serializer
        read_serializer = ExamSerializer(instance)
        headers = self.get_success_headers(read_serializer.data)

        return Response(
            read_serializer.data,
            status=201,
            headers=headers,
        )

    # ================================
    # Immutable 필드 방어
    # ================================
    def _reject_immutable_fields_on_update(self, request):
        forbidden = {"exam_type", "subject"}
        incoming = set(request.data.keys())
        bad = sorted(list(incoming & forbidden))
        if bad:
            raise ValidationError(
                {"detail": f"Immutable fields in update are forbidden: {bad}"}
            )
        # template_exam_id는 regular 생성 시 미지정 가능 → 시험 설정에서 한 번 지정 허용

    # ================================
    # CREATE 로직
    # ================================
    def perform_create(self, serializer):
        exam_type = serializer.validated_data.get("exam_type")

        # =========================
        # TEMPLATE CREATE
        # =========================
        if exam_type == Exam.ExamType.TEMPLATE:
            if self.request.data.get("session_id"):
                raise ValidationError(
                    {"session_id": "template exam must not receive session_id"}
                )
            if self.request.data.get("template_exam_id"):
                raise ValidationError(
                    {"template_exam_id": "template exam must not receive template_exam_id"}
                )

            serializer.save(
                exam_type=Exam.ExamType.TEMPLATE,
                template_exam=None,
            )
            return

        # =========================
        # REGULAR CREATE
        # =========================
        template_exam_id = self.request.data.get("template_exam_id")
        template_exam = None
        subject = ""

        if template_exam_id:
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
            subject = template_exam.subject

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

        tenant = getattr(self.request, "tenant", None)
        if tenant and getattr(session, "lecture", None) and session.lecture.tenant_id != tenant.id:
            raise PermissionDenied("Session does not belong to your program.")

        exam = serializer.save(
            exam_type=Exam.ExamType.REGULAR,
            subject=subject,
            template_exam=template_exam,
        )

        exam.sessions.add(session)

    # ================================
    # UPDATE 방어
    # ================================
    def update(self, request, *args, **kwargs):
        self._reject_immutable_fields_on_update(request)
        return super().update(request, *args, **kwargs)

    def partial_update(self, request, *args, **kwargs):
        self._reject_immutable_fields_on_update(request)
        return super().partial_update(request, *args, **kwargs)

    # ================================
    # DELETE 봉인
    # ================================
    def destroy(self, request, *args, **kwargs):
        obj: Exam = self.get_object()

        if obj.exam_type == Exam.ExamType.TEMPLATE and obj.derived_exams.exists():
            raise PermissionDenied(
                "This template is used by regular exams and cannot be deleted."
            )

        return super().destroy(request, *args, **kwargs)

    # ================================
    # Query Filters
    # ================================
    def get_queryset(self):
        tenant = getattr(self.request, "tenant", None)
        if not tenant:
            return Exam.objects.none()
        qs = Exam.objects.filter(sessions__lecture__tenant=tenant).distinct()

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

        return qs.order_by("-created_at")
