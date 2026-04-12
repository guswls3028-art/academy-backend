# PATH: apps/core/views/profile.py
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from drf_yasg.utils import swagger_auto_schema

from apps.core.permissions import TenantResolvedAndStaff
from apps.core.serializers import ProfileSerializer


# --------------------------------------------------
# Profile (Staff 영역)
# --------------------------------------------------

class ProfileViewSet(viewsets.ViewSet):
    """
    직원/강사/관리자 전용 Profile API
    """

    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    @swagger_auto_schema(auto_schema=None)
    @action(detail=False, methods=["get"])
    def me(self, request):
        serializer = ProfileSerializer(request.user)
        return Response(serializer.data)

    @swagger_auto_schema(auto_schema=None)
    @action(detail=False, methods=["patch"])
    def update_me(self, request):
        from apps.core.models.user import user_internal_username, user_display_username, User

        # ── username 변경 처리 ──
        new_username = request.data.get("username")
        if new_username is not None:
            new_username = str(new_username).strip()
            if not new_username:
                return Response({"detail": "아이디는 비어있을 수 없습니다."}, status=400)
            tenant = getattr(request, "tenant", None)
            if not tenant:
                return Response({"detail": "tenant must be resolved"}, status=400)
            internal = user_internal_username(tenant, new_username)
            if User.objects.filter(username=internal).exclude(pk=request.user.pk).exists():
                return Response({"detail": "이미 사용 중인 아이디입니다."}, status=400)
            request.user.username = internal
            request.user.save(update_fields=["username"])

        # ── name / phone 변경 처리 ──
        serializer = ProfileSerializer(
            request.user,
            data=request.data,
            partial=True,
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()

        # username을 display 형태로 포함하여 반환
        resp = serializer.data
        resp["username"] = user_display_username(request.user)
        return Response(resp)

    @swagger_auto_schema(auto_schema=None)
    @action(detail=False, methods=["post"], url_path="change-password")
    def change_password(self, request):
        old_pw = request.data.get("old_password")
        new_pw = request.data.get("new_password")

        if not old_pw or not new_pw:
            return Response({"error": "old_password, new_password 필요"}, status=400)

        if not request.user.check_password(old_pw):
            return Response({"error": "현재 비밀번호가 올바르지 않습니다."}, status=400)

        request.user.set_password(new_pw)
        request.user.save()

        return Response({"message": "비밀번호 변경 완료"})
