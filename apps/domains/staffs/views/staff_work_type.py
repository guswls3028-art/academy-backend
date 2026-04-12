# PATH: apps/domains/staffs/views/staff_work_type.py

from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import viewsets
from rest_framework.filters import OrderingFilter
from rest_framework.permissions import IsAuthenticated

from ..serializers import StaffWorkTypeSerializer
from academy.adapters.db.django import repositories_staffs as staff_repo
from .helpers import IsPayrollManager

# ===========================
# StaffWorkType
# ===========================

class StaffWorkTypeViewSet(viewsets.ModelViewSet):
    serializer_class = StaffWorkTypeSerializer
    permission_classes = [IsAuthenticated, IsPayrollManager]

    filter_backends = (DjangoFilterBackend, OrderingFilter)
    filterset_fields = ["staff", "work_type"]
    ordering_fields = ["created_at"]

    def get_queryset(self):
        return staff_repo.staff_work_type_queryset_tenant(self.request.tenant)

    def perform_create(self, serializer):
        serializer.save(tenant=self.request.tenant)
