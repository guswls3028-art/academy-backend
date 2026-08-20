# PATH: apps/domains/clinic/views/assessment_views.py
from rest_framework import serializers, viewsets
from rest_framework.filters import OrderingFilter, SearchFilter
from rest_framework.permissions import IsAuthenticated

from django_filters.rest_framework import DjangoFilterBackend

from apps.core.permissions import TenantResolvedAndStaff

from ..models import Test
from ..serializers import ClinicTestSerializer


class TestViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]
    serializer_class = ClinicTestSerializer
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    search_fields = ["title"]
    ordering_fields = ["date", "created_at", "id"]
    ordering = ["-date", "-created_at", "-id"]

    def get_queryset(self):
        tenant = getattr(self.request, "tenant", None)
        if not tenant:
            raise serializers.ValidationError(
                {"tenant": "테넌트 컨텍스트가 필요합니다. (호스트 또는 X-Tenant-Code 확인)"}
            )
        return Test.objects.filter(tenant=tenant).select_related("session")

    def perform_create(self, serializer):
        tenant = getattr(self.request, "tenant", None)
        if not tenant:
            raise serializers.ValidationError(
                {"tenant": "테넌트 컨텍스트가 필요합니다."}
            )
        session = serializer.validated_data.get("session")
        if session and session.tenant_id != tenant.id:
            raise serializers.ValidationError(
                {"session": "해당 세션이 현재 학원에 속하지 않습니다."}
            )
        serializer.save(tenant=tenant)
