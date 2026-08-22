# PATH: apps/core/views/profile.py
from django.db import IntegrityError, transaction
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from drf_yasg.utils import swagger_auto_schema

from apps.core.permissions import TenantResolvedAndStaff
from apps.core.serializers import ProfileSerializer
from apps.api.common.throttles import ChangePasswordThrottle


# --------------------------------------------------
# Profile (Staff 영역)
# --------------------------------------------------

class ProfileViewSet(viewsets.ViewSet):
    """
    직원/강사/관리자 전용 Profile API
    """

    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def get_throttles(self):
        if getattr(self, "action", None) == "change_password":
            return [ChangePasswordThrottle()]
        return super().get_throttles()

    @swagger_auto_schema(auto_schema=None)
    @action(detail=False, methods=["get"])
    def me(self, request):
        serializer = ProfileSerializer(request.user)
        return Response(serializer.data)

    @swagger_auto_schema(auto_schema=None)
    @action(detail=False, methods=["patch"])
    def update_me(self, request):
        from apps.core.models.user import user_internal_username, user_display_username, User

        serializer = ProfileSerializer(
            request.user,
            data=request.data,
            partial=True,
        )
        serializer.is_valid(raise_exception=True)

        new_username = request.data.get("username")
        internal = None
        if new_username is not None:
            new_username = str(new_username).strip()
            if not new_username:
                return Response({"detail": "아이디는 비어있을 수 없습니다."}, status=400)
            tenant = getattr(request, "tenant", None)
            if not tenant:
                return Response({"detail": "tenant must be resolved"}, status=400)
            internal = user_internal_username(tenant, new_username)

        try:
            with transaction.atomic():
                locked_user = User.objects.select_for_update().get(pk=request.user.pk)
                if internal and User.objects.filter(username=internal).exclude(pk=locked_user.pk).exists():
                    return Response({"detail": "이미 사용 중인 아이디입니다."}, status=400)
                serializer = ProfileSerializer(
                    locked_user,
                    data=request.data,
                    partial=True,
                )
                serializer.is_valid(raise_exception=True)
                serializer.save(**({"username": internal} if internal else {}))
        except IntegrityError:
            return Response({"detail": "이미 사용 중인 아이디입니다."}, status=400)

        # username을 display 형태로 포함하여 반환
        resp = serializer.data
        resp["username"] = user_display_username(serializer.instance)
        return Response(resp)

    @swagger_auto_schema(auto_schema=None)
    @action(detail=False, methods=["post"], url_path="change-password")
    def change_password(self, request):
        old_pw = request.data.get("old_password")
        new_pw = request.data.get("new_password")

        if not old_pw or not new_pw:
            return Response({"error": "old_password, new_password 필요"}, status=400)

        if len(new_pw) < 4:
            return Response({"error": "새 비밀번호는 4자 이상이어야 합니다."}, status=400)

        if old_pw == new_pw:
            return Response({"error": "새 비밀번호가 현재 비밀번호와 같습니다."}, status=400)

        from apps.core.services.password import (
            CurrentPasswordMismatch,
            PasswordNoticeDeliveryError,
            change_password_with_notice,
        )
        from apps.domains.students.services.account_notifications import (
            send_user_password_changed_notice,
        )
        try:
            change_password_with_notice(
                request.user,
                current_password=str(old_pw),
                new_password=str(new_pw),
                send_notice=send_user_password_changed_notice,
            )
        except CurrentPasswordMismatch:
            return Response({"error": "현재 비밀번호가 올바르지 않습니다."}, status=400)
        except PasswordNoticeDeliveryError as exc:
            return Response({"error": str(exc)}, status=503)

        return Response({"message": "비밀번호 변경 완료"})
