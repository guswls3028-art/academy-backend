from __future__ import annotations

from django.db.models import Q

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
            # 다른 도메인과 일관: list/retrieve도 테넌트 멤버 검증을 거쳐야 한다
            # (queryset이 테넌트 스코프이지만, 헤더 기반 X-Tenant-Code 변조 시
            #  비멤버 인증사용자가 도달할 수 있어 1차 게이트를 추가).
            return [IsAuthenticated(), TenantResolvedAndMember()]
        return [IsAuthenticated(), TenantResolvedAndMember(), IsTeacherOrAdmin()]

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
        tenant = getattr(self.request, "tenant", None)
        if not tenant:
            raise PermissionDenied("Tenant is required.")

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
                tenant=tenant,
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
                template_exam = Exam.objects.filter(tenant=tenant).get(id=template_exam_id)
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
            session = Session.objects.get(id=session_id, lecture__tenant=tenant)
        except Session.DoesNotExist:
            raise ValidationError({"session_id": "invalid"})

        # 템플릿 없이 생성 시 강의(Lecture) 과목을 시험 과목으로 자동 반영
        if not subject and getattr(session, "lecture", None):
            subject = (getattr(session.lecture, "subject", None) or "").strip()

        exam = serializer.save(
            exam_type=Exam.ExamType.REGULAR,
            subject=subject,
            template_exam=template_exam,
            tenant=tenant,
        )

        exam.sessions.add(session)

    # ================================
    # UPDATE 방어 + pass_score 변경 시 ClinicLink 해소 재계산
    # ================================
    def update(self, request, *args, **kwargs):
        self._reject_immutable_fields_on_update(request)
        return self._update_with_recalc(super().update, request, *args, **kwargs)

    def partial_update(self, request, *args, **kwargs):
        self._reject_immutable_fields_on_update(request)
        return self._update_with_recalc(super().partial_update, request, *args, **kwargs)

    def _update_with_recalc(self, upstream, request, *args, **kwargs):
        """
        2026-05-13: pass_score 변경 시 progress pipeline 재실행.
        ClinicTriggerService.auto_create_per_exam 가 exam_meta.passed 기준으로
        ClinicLink 생성/해소를 idempotent 하게 처리하므로, pipeline 만 트리거하면
        하향(예: 70→50) 시 PASS 학생의 미해소 ClinicLink 가 자동 해소됨.
        """
        try:
            obj: Exam = self.get_object()
            prev_pass = float(getattr(obj, "pass_score", 0) or 0)
        except Exception:
            prev_pass = None

        response = upstream(request, *args, **kwargs)

        try:
            new_pass = response.data.get("pass_score") if hasattr(response, "data") else None
            if prev_pass is not None and new_pass is not None and float(new_pass) != prev_pass:
                from apps.domains.progress.dispatcher import dispatch_progress_pipeline
                exam_id_for_pipeline = int(response.data.get("id") or kwargs.get("pk") or 0)
                if exam_id_for_pipeline:
                    dispatch_progress_pipeline(exam_id=exam_id_for_pipeline)
        except Exception:
            # progress pipeline 실패해도 update 자체는 유지 (응답 반영됨)
            import logging
            logging.getLogger(__name__).exception(
                "ExamViewSet update: progress dispatch after pass_score change failed"
            )

        return response

    # ================================
    # DELETE 봉인
    # ================================
    def _regular_delete_blocker(self, obj: Exam) -> str | None:
        from apps.domains.results.models import Result, ResultFact
        from apps.domains.submissions.models import Submission

        if obj.exam_enrollments.exists():
            return "exam enrollments"
        if obj.attempts.exists():
            return "exam attempts"
        if Submission.objects.filter(
            tenant=obj.tenant,
            target_type=Submission.TargetType.EXAM,
            target_id=obj.id,
        ).exists():
            return "submissions"
        if obj.results.exists():
            return "exam results"
        if Result.objects.filter(target_type="exam", target_id=obj.id).exists():
            return "results"
        if ResultFact.objects.filter(target_type="exam", target_id=obj.id).exists():
            return "result facts"
        return None

    def destroy(self, request, *args, **kwargs):
        obj: Exam = self.get_object()

        if obj.exam_type == Exam.ExamType.TEMPLATE and obj.derived_exams.exists():
            raise PermissionDenied(
                "This template is used by regular exams and cannot be deleted."
            )

        if obj.exam_type == Exam.ExamType.REGULAR:
            blocker = self._regular_delete_blocker(obj)
            if blocker:
                raise PermissionDenied(
                    f"This regular exam has {blocker} and cannot be deleted."
                )

        return super().destroy(request, *args, **kwargs)

    # ================================
    # Query Filters
    # ================================
    def get_queryset(self):
        tenant = getattr(self.request, "tenant", None)
        if not tenant:
            return Exam.objects.none()
        qs = Exam.objects.filter(
            Q(sessions__lecture__tenant=tenant) | Q(tenant=tenant)
        ).distinct()

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
