# PATH: apps/domains/staffs/views/work_type.py

from django.db import transaction
from django.db.models import ProtectedError
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import viewsets
from rest_framework.filters import SearchFilter, OrderingFilter
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import ValidationError

from ..serializers import WorkTypeSerializer
from academy.adapters.db.django import repositories_staffs as staff_repo
from .helpers import IsPayrollManager, StaffDomainPagination

# ===========================
# WorkType
# ===========================

class WorkTypeViewSet(viewsets.ModelViewSet):
    serializer_class = WorkTypeSerializer
    permission_classes = [IsAuthenticated, IsPayrollManager]
    pagination_class = StaffDomainPagination

    filter_backends = (DjangoFilterBackend, SearchFilter, OrderingFilter)
    filterset_fields = ["is_active"]
    search_fields = ["name", "description"]
    ordering_fields = ["name", "base_hourly_wage", "created_at"]

    def get_queryset(self):
        return staff_repo.work_type_queryset_tenant(self.request.tenant)

    def perform_create(self, serializer):
        work_type = serializer.save(tenant=self.request.tenant)
        from apps.core.services.ops_audit import record_audit

        record_audit(
            self.request,
            action="staff.work_type_created",
            target_tenant=self.request.tenant,
            summary=f"work_type_id={work_type.id}",
            payload={
                "work_type_id": work_type.id,
                "base_hourly_wage": work_type.base_hourly_wage,
            },
        )

    def perform_update(self, serializer):
        stale_work_type = serializer.instance
        with transaction.atomic():
            work_type = (
                stale_work_type.__class__.objects.select_for_update().get(
                    tenant=self.request.tenant,
                    id=stale_work_type.id,
                )
            )
            serializer.instance = work_type
            old_wage = work_type.base_hourly_wage
            old_active = work_type.is_active
            work_type = serializer.save()
        from apps.core.services.ops_audit import record_audit

        record_audit(
            self.request,
            action="staff.work_type_updated",
            target_tenant=self.request.tenant,
            summary=f"work_type_id={work_type.id}",
            payload={
                "work_type_id": work_type.id,
                "old_base_hourly_wage": old_wage,
                "new_base_hourly_wage": work_type.base_hourly_wage,
                "old_is_active": old_active,
                "new_is_active": work_type.is_active,
            },
        )

    def perform_destroy(self, instance):
        try:
            with transaction.atomic():
                instance = (
                    instance.__class__.objects.select_for_update().get(
                        tenant=self.request.tenant,
                        id=instance.id,
                    )
                )
                work_type_id = instance.id
                work_type_name = instance.name
                instance.delete()
        except ProtectedError:
            raise ValidationError(
                {"detail": f'"{instance.name}" 시급태그를 사용하는 근무기록이 있어 삭제할 수 없습니다. 비활성으로 변경해 주세요.'}
            )
        from apps.core.services.ops_audit import record_audit

        record_audit(
            self.request,
            action="staff.work_type_deleted",
            target_tenant=self.request.tenant,
            summary=f"work_type_id={work_type_id}",
            payload={
                "work_type_id": work_type_id,
                "name": work_type_name,
            },
        )
