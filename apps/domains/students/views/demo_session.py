from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import NotFound
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from apps.core.models import TenantMembership
from apps.domains.students.models import Student


class DemoRoleInput(serializers.Serializer):
    role = serializers.ChoiceField(choices=["teacher", "student"])


class DawonDemoSessionView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [AnonRateThrottle]

    def post(self, request):
        tenant = getattr(request, "tenant", None)
        if not getattr(settings, "DAWON_REVIEW_CLASS_GROUPS", False) or not tenant or (tenant.pk, tenant.code) != (719, "dawon"):
            raise NotFound()

        intent = DemoRoleInput(data=request.data)
        intent.is_valid(raise_exception=True)
        role = intent.validated_data["role"]
        if role == "teacher":
            user = get_user_model().objects.filter(tenant=tenant, username="t719_dawon-demo-teacher", is_active=True).first()
            expected = "teacher"
        else:
            sample = Student.objects.filter(
                tenant=tenant, pk=2, name="다원 수업 검수 학생 2", deleted_at__isnull=True, is_managed=True,
            ).select_related("user").first()
            user = sample.user if sample else None
            expected = "student"
        if not user or not user.is_active or not TenantMembership.objects.filter(
            tenant=tenant, user=user, role=expected, is_active=True,
        ).exists():
            raise NotFound("체험 계정을 확인할 수 없습니다.")

        end = timezone.now() + timedelta(hours=2)
        jwt = RefreshToken.for_user(user)
        jwt["tenant_id"] = tenant.pk
        jwt["token_version"] = user.token_version
        jwt["mcp"] = False
        jwt["dawon_demo_role"] = role
        jwt["dawon_demo_until"] = int(end.timestamp())
        jwt.set_exp(lifetime=timedelta(hours=2))
        response = Response({"access": str(jwt.access_token), "refresh": str(jwt)})
        response["Cache-Control"] = "no-store"
        return response
