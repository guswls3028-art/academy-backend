from rest_framework.viewsets import ModelViewSet
from rest_framework.exceptions import PermissionDenied
from django_filters.rest_framework import DjangoFilterBackend

from apps.core.permissions import TenantResolvedAndMember
from .models import Dday
from .serializers import DdaySerializer


class DdayViewSet(ModelViewSet):
    serializer_class = DdaySerializer
    permission_classes = [TenantResolvedAndMember]

    filter_backends = [DjangoFilterBackend]
    filterset_fields = ["lecture"]

    def get_queryset(self):
        tenant = getattr(self.request, "tenant", None)
        if not tenant:
            return Dday.objects.none()
        return Dday.objects.filter(lecture__tenant=tenant)

    def perform_create(self, serializer):
        tenant = getattr(self.request, "tenant", None)
        if not tenant:
            raise PermissionDenied("Tenant required")
        lecture = serializer.validated_data.get("lecture")
        if lecture is not None and getattr(lecture, "tenant_id", None) != tenant.id:
            raise PermissionDenied("Lecture does not belong to your program.")
        serializer.save()
