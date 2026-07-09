# PATH: apps/core/views/auth.py
import logging
from django.conf import settings

from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from drf_yasg.utils import swagger_auto_schema

from apps.core.permissions import TenantResolvedAndMember
from apps.core.serializers import UserSerializer
from apps.api.common.throttles import ChangePasswordThrottle

logger = logging.getLogger(__name__)


# --------------------------------------------------
# Auth: /core/me/
# --------------------------------------------------

class ChangePasswordView(APIView):
    """
    전 역할(학부모/학생/직원) 비밀번호 변경.
    must_change_password 플래그도 해제.
    """

    permission_classes = [IsAuthenticated, TenantResolvedAndMember]
    throttle_classes = [ChangePasswordThrottle]

    @swagger_auto_schema(auto_schema=None)
    def post(self, request):
        old_pw = request.data.get("old_password")
        new_pw = request.data.get("new_password")

        if not old_pw or not new_pw:
            return Response({"detail": "현재 비밀번호와 새 비밀번호를 모두 입력해 주세요."}, status=400)

        if len(new_pw) < 4:
            return Response({"detail": "새 비밀번호는 4자 이상이어야 합니다."}, status=400)

        if old_pw == new_pw:
            return Response({"detail": "새 비밀번호가 현재 비밀번호와 같습니다."}, status=400)

        if not request.user.check_password(old_pw):
            return Response({"detail": "현재 비밀번호가 올바르지 않습니다."}, status=400)

        from apps.core.services.password import change_password, rollback_password
        from apps.domains.students.services.account_notifications import (
            send_user_password_changed_notice,
        )
        previous_password_hash = request.user.password
        previous_must_change_password = bool(getattr(request.user, "must_change_password", False))
        change_password(request.user, new_pw)
        if not send_user_password_changed_notice(user=request.user, password=str(new_pw)):
            rollback_password(
                request.user,
                previous_password_hash,
                must_change_password=previous_must_change_password,
            )
            return Response({"detail": "비밀번호 변경 알림톡 발송에 실패했습니다. 잠시 후 다시 시도해 주세요."}, status=503)

        return Response({"message": "비밀번호가 변경되었습니다."})


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
