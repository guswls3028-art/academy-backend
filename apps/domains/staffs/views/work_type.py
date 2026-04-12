# PATH: apps/domains/staffs/views/work_type.py

from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import viewsets
from rest_framework.filters import SearchFilter, OrderingFilter
from rest_framework.permissions import IsAuthenticated

from ..serializers import WorkTypeSerializer
from academy.adapters.db.django import repositories_staffs as staff_repo
from .helpers import IsPayrollManager

# ===========================
# WorkType
# ===========================

class WorkTypeViewSet(viewsets.ModelViewSet):
    serializer_class = WorkTypeSerializer
    permission_classes = [IsAuthenticated, IsPayrollManager]

    filter_backends = (DjangoFilterBackend, SearchFilter, OrderingFilter)
    filterset_fields = ["is_active"]
    search_fields = ["name", "description"]
    ordering_fields = ["name", "base_hourly_wage", "created_at"]

    def get_queryset(self):
        return staff_repo.work_type_queryset_tenant(self.request.tenant)

    def perform_create(self, serializer):
        serializer.save(tenant=self.request.tenant)
