from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import TenantResolvedAndStaff
from apps.core.services.initial_password_policy import password_settings, save_password_settings


class AccountPasswordSettingsSchema(serializers.Serializer):
    student_mode = serializers.ChoiceField(choices=("phone_last4", "fixed", "random"), required=False, allow_null=True)
    parent_mode = serializers.ChoiceField(choices=("phone_last4", "fixed", "random"), required=False, allow_null=True)
    student_fixed_password = serializers.CharField(required=False, allow_blank=True, trim_whitespace=False)
    parent_fixed_password = serializers.CharField(required=False, allow_blank=True, trim_whitespace=False)


class InitialPasswordSettingsView(APIView):
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    @extend_schema(responses={200: AccountPasswordSettingsSchema})
    def get(self, request):
        return Response(password_settings(request.tenant))

    @extend_schema(request=AccountPasswordSettingsSchema, responses={200: AccountPasswordSettingsSchema})
    def patch(self, request):
        if not isinstance(request.data, dict):
            return Response({"detail": "비밀번호 설정을 확인해 주세요."}, status=400)
        payload = AccountPasswordSettingsSchema(data=request.data, partial=True)
        payload.is_valid(raise_exception=True)
        try:
            return Response(save_password_settings(request.tenant, request.data))
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=400)
