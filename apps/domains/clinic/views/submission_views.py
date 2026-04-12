# PATH: apps/domains/clinic/views/submission_views.py
from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework import serializers

from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import SearchFilter, OrderingFilter

from ..models import Submission
from ..serializers import ClinicSubmissionSerializer
from ..filters import SubmissionFilter

from apps.core.permissions import TenantResolvedAndStaff


# ============================================================
# Submission
# ============================================================
class SubmissionViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]
    serializer_class = ClinicSubmissionSerializer
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_class = SubmissionFilter
    search_fields = ["student__name", "test__title"]
    ordering_fields = ["created_at"]
    ordering = ["-created_at"]

    def get_queryset(self):
        tenant = getattr(self.request, "tenant", None)
        if not tenant:
            raise serializers.ValidationError(
                {"tenant": "테넌트 컨텍스트가 필요합니다. (호스트 또는 X-Tenant-Code 확인)"}
            )
        return (
            Submission.objects
            .filter(tenant=tenant)
            .select_related("student", "test", "test__session")
        )

    def perform_create(self, serializer):
        tenant = getattr(self.request, "tenant", None)
        if not tenant:
            raise serializers.ValidationError({"tenant": "테넌트 컨텍스트가 필요합니다."})
        # P1 수정: test/student FK가 현재 테넌트 소속인지 검증
        test = serializer.validated_data.get("test")
        if test and test.tenant_id != tenant.id:
            raise serializers.ValidationError({"test": "해당 시험이 현재 학원에 속하지 않습니다."})
        student = serializer.validated_data.get("student")
        if student and student.tenant_id != tenant.id:
            raise serializers.ValidationError({"student": "해당 학생이 현재 학원에 속하지 않습니다."})
        serializer.save(tenant=tenant)
