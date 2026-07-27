# PATH: apps/domains/staffs/views/staff_work_type.py

from django.db import transaction
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import viewsets
from rest_framework.filters import OrderingFilter
from rest_framework.permissions import IsAuthenticated

from ..serializers import StaffWorkTypeSerializer
from academy.adapters.db.django import repositories_staffs as staff_repo
from .helpers import IsPayrollManager, StaffDomainPagination

# ===========================
# StaffWorkType
# ===========================

class StaffWorkTypeViewSet(viewsets.ModelViewSet):
    serializer_class = StaffWorkTypeSerializer
    permission_classes = [IsAuthenticated, IsPayrollManager]
    pagination_class = StaffDomainPagination

    filter_backends = (DjangoFilterBackend, OrderingFilter)
    filterset_fields = ["staff", "work_type"]
    ordering_fields = ["created_at"]

    def get_queryset(self):
        return staff_repo.staff_work_type_queryset_tenant(self.request.tenant)

    def perform_create(self, serializer):
        staff = serializer.validated_data["staff"]
        with transaction.atomic():
            locked_staff = staff_repo.staff_get_for_update(
                self.request.tenant.id,
                staff.id,
            )
            assignment = serializer.save(
                tenant=self.request.tenant,
                staff=locked_staff,
            )
        self._record_assignment_audit(
            "staff.staff_work_type_created",
            assignment,
            old_hourly_wage=None,
        )

    def perform_update(self, serializer):
        stale_assignment = serializer.instance
        with transaction.atomic():
            staff_repo.staff_get_for_update(
                stale_assignment.tenant_id,
                stale_assignment.staff_id,
            )
            assignment = staff_repo.staff_work_type_get_for_update(
                stale_assignment.tenant_id,
                stale_assignment.id,
            )
            serializer.instance = assignment
            old_hourly_wage = assignment.hourly_wage
            assignment = serializer.save()
        self._record_assignment_audit(
            "staff.staff_work_type_updated",
            assignment,
            old_hourly_wage=old_hourly_wage,
        )

    def perform_destroy(self, instance):
        with transaction.atomic():
            staff_repo.staff_get_for_update(
                instance.tenant_id,
                instance.staff_id,
            )
            instance = staff_repo.staff_work_type_get_for_update(
                instance.tenant_id,
                instance.id,
            )
            payload = {
                "assignment_id": instance.id,
                "staff_id": instance.staff_id,
                "work_type_id": instance.work_type_id,
                "old_hourly_wage": instance.hourly_wage,
            }
            assignment_id = instance.id
            instance.delete()
        from apps.core.services.ops_audit import record_audit

        record_audit(
            self.request,
            action="staff.staff_work_type_deleted",
            target_tenant=self.request.tenant,
            summary=f"assignment_id={assignment_id}",
            payload=payload,
        )

    def _record_assignment_audit(
        self,
        action,
        assignment,
        *,
        old_hourly_wage,
    ):
        from apps.core.services.ops_audit import record_audit

        record_audit(
            self.request,
            action=action,
            target_tenant=self.request.tenant,
            summary=f"assignment_id={assignment.id}",
            payload={
                "assignment_id": assignment.id,
                "staff_id": assignment.staff_id,
                "work_type_id": assignment.work_type_id,
                "old_hourly_wage": old_hourly_wage,
                "new_hourly_wage": assignment.hourly_wage,
                "effective_hourly_wage": assignment.effective_hourly_wage,
            },
        )
