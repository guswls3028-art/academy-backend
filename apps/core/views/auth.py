# PATH: apps/core/views/auth.py
import logging
from django.conf import settings

from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from drf_yasg.utils import swagger_auto_schema

from apps.core.permissions import TenantResolvedAndMember
from apps.core.serializers import UserSerializer

logger = logging.getLogger(__name__)


# --------------------------------------------------
# Auth: /core/me/
# --------------------------------------------------

class MeView(APIView):
    """
    ✅ Core Auth Endpoint (Enterprise Final)

    - 인증 필수
    - tenant 확정 필수
    - TenantMembership 존재 필수
    - tenant 기준 role 을 tenantRole 로 반환
    - 프론트는 이 응답만 신뢰 (SSOT)
    """

    permission_classes = [IsAuthenticated, TenantResolvedAndMember]

    @swagger_auto_schema(auto_schema=None)
    def get(self, request):
        try:
            serializer = UserSerializer(
                request.user,
                context={"request": request},  # ✅ 핵심
            )
            return Response(serializer.data)
        except Exception as e:
            logger.exception("MeView get failed: %s", e)
            payload = {"detail": "서버 오류가 발생했습니다."}
            if getattr(settings, "DEBUG", False):
                payload["error"] = str(e)
            return Response(
                payload,
                status=500,
            )
