# apps/domains/progress/views.py
from rest_framework import status as drf_status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet
from rest_framework.permissions import IsAuthenticated
from rest_framework.filters import SearchFilter, OrderingFilter
from django_filters.rest_framework import DjangoFilterBackend

from apps.core.permissions import TenantResolvedAndMember, TenantResolvedAndStaff
from .models import ProgressPolicy, SessionProgress, LectureProgress, ClinicLink, RiskLog
from .serializers import (
    ProgressPolicySerializer,
    SessionProgressSerializer,
    LectureProgressSerializer,
    ClinicLinkSerializer,
    RiskLogSerializer,
)
from .filters import (
    ProgressPolicyFilter,
    SessionProgressFilter,
    LectureProgressFilter,
    ClinicLinkFilter,
    RiskLogFilter,
)
from .services.clinic_resolution_service import ClinicResolutionService
from .services.clinic_remediation_service import ClinicRemediationService


class ProgressPolicyViewSet(ModelViewSet):
    serializer_class = ProgressPolicySerializer
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def get_permissions(self):
        if self.action in ("list", "retrieve"):
            return [IsAuthenticated(), TenantResolvedAndMember()]
        return [IsAuthenticated(), TenantResolvedAndStaff()]

    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_class = ProgressPolicyFilter
    search_fields = ["lecture__title", "lecture__name"]
    ordering_fields = ["id", "created_at", "updated_at"]
    ordering = ["-id"]

    def get_queryset(self):
        tenant = getattr(self.request, "tenant", None)
        if not tenant:
            return ProgressPolicy.objects.none()
        return ProgressPolicy.objects.filter(lecture__tenant=tenant).select_related("lecture")


class SessionProgressViewSet(ModelViewSet):
    serializer_class = SessionProgressSerializer
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def get_permissions(self):
        if self.action in ("list", "retrieve"):
            return [IsAuthenticated(), TenantResolvedAndMember()]
        return [IsAuthenticated(), TenantResolvedAndStaff()]

    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_class = SessionProgressFilter
    search_fields = ["enrollment_id", "session__title", "session__lecture__title"]
    ordering_fields = ["id", "created_at", "updated_at", "calculated_at", "completed"]
    ordering = ["-updated_at", "-id"]

    def get_queryset(self):
        tenant = getattr(self.request, "tenant", None)
        if not tenant:
            return SessionProgress.objects.none()
        return SessionProgress.objects.filter(session__lecture__tenant=tenant).select_related("session", "session__lecture")


class LectureProgressViewSet(ModelViewSet):
    serializer_class = LectureProgressSerializer
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def get_permissions(self):
        if self.action in ("list", "retrieve"):
            return [IsAuthenticated(), TenantResolvedAndMember()]
        return [IsAuthenticated(), TenantResolvedAndStaff()]

    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_class = LectureProgressFilter
    search_fields = ["enrollment_id", "lecture__title", "lecture__name"]
    ordering_fields = ["id", "created_at", "updated_at", "risk_level", "completed_sessions"]
    ordering = ["-updated_at", "-id"]

    def get_queryset(self):
        tenant = getattr(self.request, "tenant", None)
        if not tenant:
            return LectureProgress.objects.none()
        return LectureProgress.objects.filter(lecture__tenant=tenant).select_related("lecture", "last_session")


class ClinicLinkViewSet(ModelViewSet):
    serializer_class = ClinicLinkSerializer
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def get_permissions(self):
        if self.action in ("list", "retrieve"):
            return [IsAuthenticated(), TenantResolvedAndMember()]
        return [IsAuthenticated(), TenantResolvedAndStaff()]

    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_class = ClinicLinkFilter
    search_fields = ["enrollment_id", "session__title", "session__lecture__title", "memo"]
    ordering_fields = ["id", "created_at", "updated_at", "approved", "is_auto", "cycle_no", "resolution_type"]
    ordering = ["-created_at", "-id"]

    def get_queryset(self):
        tenant = getattr(self.request, "tenant", None)
        if not tenant:
            return ClinicLink.objects.none()
        qs = ClinicLink.objects.filter(
            tenant=tenant,
        ).select_related("session", "session__lecture", "enrollment__student")

        # 추가 필터: unresolved_only
        unresolved_only = self.request.query_params.get("unresolved_only")
        if unresolved_only in ("true", "1"):
            qs = qs.filter(resolved_at__isnull=True)

        return qs

    @action(detail=True, methods=["post"])
    def resolve(self, request, pk=None):
        """
        POST /progress/clinic-links/{id}/resolve/
        관리자 수동 해소. body: { "memo": "..." }
        """
        link = self.get_object()
        if link.resolved_at:
            return Response(
                {"detail": "이미 해소된 항목입니다."},
                status=drf_status.HTTP_400_BAD_REQUEST,
            )

        memo = request.data.get("memo")
        result = ClinicResolutionService.resolve_manually(
            clinic_link_id=link.id,
            user_id=request.user.id,
            memo=memo,
        )
        if not result:
            return Response(
                {"detail": "해소에 실패했습니다."},
                status=drf_status.HTTP_400_BAD_REQUEST,
            )

        return Response(ClinicLinkSerializer(result).data)

    @action(detail=True, methods=["post"])
    def waive(self, request, pk=None):
        """
        POST /progress/clinic-links/{id}/waive/
        면제 처리. body: { "memo": "..." }
        """
        link = self.get_object()
        if link.resolved_at:
            return Response(
                {"detail": "이미 해소된 항목입니다."},
                status=drf_status.HTTP_400_BAD_REQUEST,
            )

        memo = request.data.get("memo")
        result = ClinicResolutionService.waive(
            clinic_link_id=link.id,
            user_id=request.user.id,
            memo=memo,
        )
        if not result:
            return Response(
                {"detail": "면제 처리에 실패했습니다."},
                status=drf_status.HTTP_400_BAD_REQUEST,
            )

        return Response(ClinicLinkSerializer(result).data)

    @action(detail=True, methods=["post"], url_path="carry-over")
    def carry_over(self, request, pk=None):
        """
        POST /progress/clinic-links/{id}/carry-over/
        다음 cycle로 이월.
        """
        link = self.get_object()
        if link.resolved_at:
            return Response(
                {"detail": "이미 해소된 항목은 이월할 수 없습니다."},
                status=drf_status.HTTP_400_BAD_REQUEST,
            )

        new_link = ClinicResolutionService.carry_over(clinic_link_id=link.id)
        if not new_link:
            return Response(
                {"detail": "이월에 실패했습니다."},
                status=drf_status.HTTP_400_BAD_REQUEST,
            )

        return Response(ClinicLinkSerializer(new_link).data, status=drf_status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], url_path="submit-retake")
    def submit_retake(self, request, pk=None):
        """
        POST /progress/clinic-links/{id}/submit-retake/
        클리닉 재시도 점수 입력.

        Body:
        - score (required): 점수
        - max_score (optional): 과제 최대 점수 (시험은 exam.max_score 자동 사용)
        """
        link = self.get_object()
        if link.resolved_at:
            return Response(
                {"detail": "이미 해소된 항목입니다."},
                status=drf_status.HTTP_400_BAD_REQUEST,
            )

        score = request.data.get("score")
        if score is None:
            return Response(
                {"detail": "score는 필수입니다."},
                status=drf_status.HTTP_400_BAD_REQUEST,
            )

        try:
            score = float(score)
        except (TypeError, ValueError):
            return Response(
                {"detail": "score는 숫자여야 합니다."},
                status=drf_status.HTTP_400_BAD_REQUEST,
            )

        if score < 0:
            return Response(
                {"detail": "score는 0 이상이어야 합니다."},
                status=drf_status.HTTP_400_BAD_REQUEST,
            )

        max_score = request.data.get("max_score")
        if max_score is not None:
            try:
                max_score = float(max_score)
            except (TypeError, ValueError):
                max_score = None

        source_type = link.source_type

        try:
            if source_type == "exam":
                result = ClinicRemediationService.submit_exam_retake(
                    clinic_link_id=link.id,
                    score=score,
                    graded_by_user_id=request.user.id,
                )
            elif source_type == "homework":
                result = ClinicRemediationService.submit_homework_retake(
                    clinic_link_id=link.id,
                    score=score,
                    max_score=max_score,
                    graded_by_user_id=request.user.id,
                )
            else:
                return Response(
                    {"detail": f"지원하지 않는 source_type: {source_type}"},
                    status=drf_status.HTTP_400_BAD_REQUEST,
                )
        except ClinicLink.DoesNotExist:
            return Response(
                {"detail": "미해소 ClinicLink를 찾을 수 없습니다."},
                status=drf_status.HTTP_404_NOT_FOUND,
            )
        except Exception as e:
            return Response(
                {"detail": str(e)},
                status=drf_status.HTTP_400_BAD_REQUEST,
            )

        return Response({
            "passed": result.passed,
            "score": result.score,
            "max_score": result.max_score,
            "attempt_index": result.attempt_index,
            "resolution_type": result.resolution_type,
            "resolved_at": result.resolved_at,
            "clinic_link_id": result.clinic_link_id,
        })

    @action(detail=True, methods=["post"], url_path="update-retake")
    def update_retake(self, request, pk=None):
        """
        POST /progress/clinic-links/{id}/update-retake/
        기존 재시도(2차+)의 점수를 수정한다.

        Body:
        - attempt_index (required): 수정할 시도 차수 (2 이상)
        - score (required): 새 점수
        - max_score (optional): 과제 최대 점수
        """
        link = self.get_object()

        attempt_index = request.data.get("attempt_index")
        score = request.data.get("score")

        if attempt_index is None or score is None:
            return Response(
                {"detail": "attempt_index와 score는 필수입니다."},
                status=drf_status.HTTP_400_BAD_REQUEST,
            )

        try:
            attempt_index = int(attempt_index)
            score = float(score)
        except (TypeError, ValueError):
            return Response(
                {"detail": "attempt_index는 정수, score는 숫자여야 합니다."},
                status=drf_status.HTTP_400_BAD_REQUEST,
            )

        if attempt_index < 2:
            return Response(
                {"detail": "1차 시도는 이 API로 수정할 수 없습니다. 성적표 편집을 사용하세요."},
                status=drf_status.HTTP_400_BAD_REQUEST,
            )

        if score < 0:
            return Response(
                {"detail": "score는 0 이상이어야 합니다."},
                status=drf_status.HTTP_400_BAD_REQUEST,
            )

        max_score = request.data.get("max_score")
        if max_score is not None:
            try:
                max_score = float(max_score)
            except (TypeError, ValueError):
                max_score = None

        source_type = link.source_type

        try:
            if source_type == "exam":
                result = ClinicRemediationService.update_exam_retake(
                    clinic_link_id=link.id,
                    attempt_index=attempt_index,
                    score=score,
                    graded_by_user_id=request.user.id,
                )
            elif source_type == "homework":
                result = ClinicRemediationService.update_homework_retake(
                    clinic_link_id=link.id,
                    attempt_index=attempt_index,
                    score=score,
                    max_score=max_score,
                    graded_by_user_id=request.user.id,
                )
            else:
                return Response(
                    {"detail": f"지원하지 않는 source_type: {source_type}"},
                    status=drf_status.HTTP_400_BAD_REQUEST,
                )
        except (ClinicLink.DoesNotExist, Exception) as e:
            return Response(
                {"detail": str(e)},
                status=drf_status.HTTP_400_BAD_REQUEST,
            )

        return Response({
            "passed": result.passed,
            "score": result.score,
            "max_score": result.max_score,
            "attempt_index": result.attempt_index,
            "resolution_type": result.resolution_type,
            "resolved_at": result.resolved_at,
            "clinic_link_id": result.clinic_link_id,
        })

    @action(detail=True, methods=["post"])
    def unresolve(self, request, pk=None):
        """
        POST /progress/clinic-links/{id}/unresolve/
        통과 취소 (되돌리기).
        """
        link = self.get_object()
        if not link.resolved_at:
            return Response(
                {"detail": "미해소 상태입니다."},
                status=drf_status.HTTP_400_BAD_REQUEST,
            )

        result = ClinicResolutionService.unresolve(clinic_link_id=link.id)
        if not result:
            return Response(
                {"detail": "해소 취소에 실패했습니다."},
                status=drf_status.HTTP_400_BAD_REQUEST,
            )

        return Response(ClinicLinkSerializer(result).data)


class RiskLogViewSet(ModelViewSet):
    serializer_class = RiskLogSerializer
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def get_permissions(self):
        if self.action in ("list", "retrieve"):
            return [IsAuthenticated(), TenantResolvedAndMember()]
        return [IsAuthenticated(), TenantResolvedAndStaff()]

    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_class = RiskLogFilter
    search_fields = ["enrollment_id", "reason"]
    ordering_fields = ["id", "created_at", "updated_at", "risk_level", "rule"]
    ordering = ["-created_at", "-id"]

    def get_queryset(self):
        tenant = getattr(self.request, "tenant", None)
        if not tenant:
            return RiskLog.objects.none()
        return RiskLog.objects.filter(session__lecture__tenant=tenant).select_related("session")
