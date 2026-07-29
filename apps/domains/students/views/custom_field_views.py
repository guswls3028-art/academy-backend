from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

from apps.core.permissions import TenantResolvedAndStaff
from apps.domains.students.models import StudentCustomFieldDefinition
from apps.domains.students.serializers import StudentCustomFieldDefinitionSerializer


class StudentCustomFieldDefinitionViewSet(ModelViewSet):
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]
    serializer_class = StudentCustomFieldDefinitionSerializer
    pagination_class = None

    def get_queryset(self):
        queryset = StudentCustomFieldDefinition.objects.filter(
            tenant=self.request.tenant,
        ).order_by("position", "id")
        active = self.request.query_params.get("active")
        if active == "true":
            queryset = queryset.filter(is_active=True)
        elif active == "false":
            queryset = queryset.filter(is_active=False)
        return queryset

    def perform_create(self, serializer):
        serializer.save(
            tenant=self.request.tenant,
            created_by=self.request.user,
        )

    def perform_destroy(self, instance):
        if instance.tenant_id != self.request.tenant.id:
            return
        instance.is_active = False
        instance.save(update_fields=["is_active", "updated_at"])

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        self.perform_destroy(instance)
        return Response(status=status.HTTP_204_NO_CONTENT)
