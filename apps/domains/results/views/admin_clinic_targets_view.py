# PATH: apps/domains/results/views/admin_clinic_targets_view.py
"""
역할
- Admin/Teacher용 클리닉 대상자 조회 API

Endpoint
- GET /results/admin/clinic-targets/

설계 계약 (중요)
- 대상자 선정 단일 진실: progress.ClinicLink(is_auto=True)
- enrollment_id 기준
- 계산/판정은 Service(ClinicTargetService)에 위임
- 응답 스키마는 AdminClinicTargetSerializer로 고정 (프론트 계약)

보류된 기능 (명시)
- pagination 필요 시 추후 DRF pagination 도입 가능
- 현재는 운영에서 "전체 대상자"가 소수라는 가정 하에 list로 반환
"""

from django.db import transaction
try:
    from drf_spectacular.utils import OpenApiResponse, extend_schema
except ModuleNotFoundError as exc:
    if exc.name != "drf_spectacular":
        raise

    class OpenApiResponse:  # type: ignore[no-redef]
        def __init__(self, *, description: str):
            self.description = description

    def extend_schema(*args, **kwargs):  # type: ignore[no-redef]
        def decorator(view):
            return view

        return decorator

from rest_framework import serializers, status as drf_status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from academy.adapters.db.django.repositories_clinic_targets import (
    explicit_not_submitted_exam_results,
)
from apps.domains.results.permissions import IsTeacherOrAdmin
from apps.domains.results.services.clinic_target_service import ClinicTargetService
from apps.domains.results.serializers.admin_clinic_target import AdminClinicTargetSerializer
from apps.support.results.clinic_target_write_dependencies import (
    waive_explicit_missing_exam_target,
)


def _query_flag(value) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


class WaiveMissingExamSerializer(serializers.Serializer):
    session_id = serializers.IntegerField(min_value=1)
    enrollment_id = serializers.IntegerField(min_value=1)
    exam_id = serializers.IntegerField(min_value=1)
    memo = serializers.CharField(min_length=2, max_length=500, trim_whitespace=True)


class WaiveMissingExamResponseSerializer(serializers.Serializer):
    clinic_link_id = serializers.IntegerField(min_value=1)
    resolution_type = serializers.ChoiceField(choices=["WAIVED"])


class AdminClinicTargetsView(APIView):
    permission_classes = [IsAuthenticated, IsTeacherOrAdmin]

    def get(self, request):
        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response([], status=200)
        section_id = request.query_params.get("section_id")
        try:
            section_id = int(section_id) if section_id else None
        except (TypeError, ValueError):
            return Response(
                {"detail": "section_id must be an integer"},
                status=drf_status.HTTP_400_BAD_REQUEST,
            )
        rows = ClinicTargetService.list_admin_targets(
            tenant=tenant,
            section_id=section_id,
            include_resolved=_query_flag(
                request.query_params.get("include_resolved")
            ),
        )
        return Response(AdminClinicTargetSerializer(rows, many=True).data)


class AdminClinicMissingExamWaiveView(APIView):
    """Record an explicit exam absence as a source-specific clinic waiver."""

    permission_classes = [IsAuthenticated, IsTeacherOrAdmin]

    @extend_schema(
        request=WaiveMissingExamSerializer,
        responses={
            200: WaiveMissingExamResponseSerializer,
            201: WaiveMissingExamResponseSerializer,
            400: OpenApiResponse(description="Invalid request fields"),
            403: OpenApiResponse(description="Tenant or role denied"),
            404: OpenApiResponse(description="Explicit missing exam target not found"),
            409: OpenApiResponse(description="Target already resolved or update conflict"),
        },
    )
    @transaction.atomic
    def post(self, request):
        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response(
                {"detail": "Tenant required"},
                status=drf_status.HTTP_403_FORBIDDEN,
            )
        payload = WaiveMissingExamSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data

        result = (
            explicit_not_submitted_exam_results(tenant=tenant)
            .select_for_update()
            .filter(
                target_id=data["exam_id"],
                enrollment_id=data["enrollment_id"],
            )
            .first()
        )
        if not result:
            return Response(
                {
                    "detail": "현재 차시에서 명시적으로 미응시 처리된 시험만 면제할 수 있습니다.",
                    "code": "MISSING_EXAM_TARGET_NOT_FOUND",
                },
                status=drf_status.HTTP_404_NOT_FOUND,
            )

        outcome = waive_explicit_missing_exam_target(
            tenant=tenant,
            session_id=data["session_id"],
            enrollment_id=data["enrollment_id"],
            exam_id=data["exam_id"],
            result_id=result.id,
            user_id=request.user.id,
            memo=data["memo"],
        )
        if outcome.code == "WAIVED":
            return Response(
                {"clinic_link_id": outcome.clinic_link_id, "resolution_type": "WAIVED"}
            )
        if outcome.code == "ALREADY_RESOLVED":
            return Response(
                {"detail": "이미 다른 방식으로 처리된 시험입니다.", "code": "ALREADY_RESOLVED"},
                status=drf_status.HTTP_409_CONFLICT,
            )
        if outcome.code == "NOT_FOUND":
            return Response(
                {
                    "detail": "현재 차시에서 명시적으로 미응시 처리된 시험만 면제할 수 있습니다.",
                    "code": "MISSING_EXAM_TARGET_NOT_FOUND",
                },
                status=drf_status.HTTP_404_NOT_FOUND,
            )
        if outcome.code == "FAILED":
            return Response(
                {"detail": "면제 처리에 실패했습니다."},
                status=drf_status.HTTP_409_CONFLICT,
            )
        return Response(
            {"clinic_link_id": outcome.clinic_link_id, "resolution_type": "WAIVED"},
            status=drf_status.HTTP_201_CREATED,
        )
