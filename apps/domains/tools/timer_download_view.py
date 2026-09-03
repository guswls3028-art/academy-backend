# PATH: apps/domains/tools/timer_download_view.py
# 서명되지 않은 레거시 PC 타이머 배포를 fail-closed로 중단한다.

from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import TenantResolvedAndStaff


class TrustedTimerDistributionRequiredSerializer(serializers.Serializer):
    code = serializers.CharField()
    detail = serializers.CharField()
    distribution = serializers.CharField()
    web_path = serializers.CharField()


class TimerDownloadView(APIView):
    """GET /api/v1/tools/timer/download/ — unsigned 레거시 배포의 종단 응답."""

    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    @extend_schema(
        responses={410: TrustedTimerDistributionRequiredSerializer},
    )
    def get(self, request):
        return Response(
            {
                "code": "trusted_timer_distribution_required",
                "detail": (
                    "서명되지 않은 Windows 타이머 배포를 중단했습니다. "
                    "도구의 웹 타이머를 사용해 주세요."
                ),
                "distribution": "web_pwa",
                "web_path": "/workspace/tools/stopwatch",
            },
            status=status.HTTP_410_GONE,
        )
