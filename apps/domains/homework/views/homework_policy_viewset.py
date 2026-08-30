# PATH: apps/domains/homework/views/homework_policy_viewset.py

from rest_framework import viewsets, status
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from apps.core.permissions import TenantResolvedAndStaff
from apps.api.common.query_params import parse_query_int

from django.db import transaction

from apps.domains.homework.models import HomeworkPolicy
from apps.domains.homework.serializers import (
    HomeworkPolicySerializer,
    HomeworkPolicyPatchSerializer,
)
from apps.support.homework.view_dependencies import (
    recalc_scores_for_policy_change,
    session_exists_for_tenant,
)


class HomeworkPolicyViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]
    serializer_class = HomeworkPolicySerializer

    def get_queryset(self):
        tenant = getattr(self.request, "tenant", None)
        qs_base = HomeworkPolicy.objects.select_related("session").filter(tenant=tenant)

        # Detail action (retrieve, partial_update 등): pk로 조회 가능하도록 전체 queryset 반환
        if self.kwargs.get("pk"):
            return qs_base

        session_id = parse_query_int(
            self.request.query_params, "session", min_value=1
        )
        if session_id is None:
            return qs_base.none()

        # tenant 미설정 시 get_or_create 시 500 방지
        if not tenant:
            return qs_base.none()

        # session 존재 및 해당 tenant 소유 여부 검증
        if not session_exists_for_tenant(session_id=session_id, tenant=tenant):
            return qs_base.none()
        return qs_base.filter(session_id=session_id)

    @transaction.atomic
    def create(self, request, *args, **kwargs):
        tenant = getattr(request, "tenant", None)
        try:
            session_id = int(request.data.get("session"))
        except (TypeError, ValueError):
            return Response(
                {"session": "유효한 차시 ID가 필요합니다."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not tenant or not session_exists_for_tenant(
            session_id=session_id,
            tenant=tenant,
        ):
            return Response(
                {"session": "해당 차시를 찾을 수 없습니다."},
                status=status.HTTP_404_NOT_FOUND,
            )

        policy, created = HomeworkPolicy.objects.get_or_create(
            tenant=tenant,
            session_id=session_id,
            defaults={
                "cutline_percent": 80,
                "cutline_mode": "PERCENT",
                "cutline_value": 80,
                "round_unit_percent": 5,
                "clinic_enabled": True,
                "clinic_on_fail": True,
            },
        )
        return Response(
            HomeworkPolicySerializer(policy).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )

    @transaction.atomic
    def partial_update(self, request, *args, **kwargs):
        obj = self.get_object()

        ser = HomeworkPolicyPatchSerializer(obj, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        ser.save()

        # 정책 변경이 결과(passed)에도 반영되도록 스냅샷 재계산
        # (프론트: policy 저장 후 session-scores invalidate 필요)
        recalc_scores_for_policy_change(policy=obj)

        return Response(
            HomeworkPolicySerializer(obj).data,
            status=status.HTTP_200_OK,
        )
