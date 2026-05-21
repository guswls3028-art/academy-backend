from rest_framework import viewsets

from apps.domains.community.api.serializers import ScopeNodeMinimalSerializer
from apps.domains.community.selectors import (
    get_scope_nodes_for_tenant,
    get_empty_scope_node_queryset,
)
from apps.core.permissions import TenantResolvedAndMember, is_effective_staff
from apps.domains.student_app.permissions import get_request_student


class ScopeNodeViewSet(viewsets.ReadOnlyModelViewSet):
    """ScopeNode list for tree. Filter by tenant (from request). Pagination disabled so frontend gets full list."""
    serializer_class = ScopeNodeMinimalSerializer
    permission_classes = [TenantResolvedAndMember]
    pagination_class = None

    def get_queryset(self):
        tenant = getattr(self.request, "tenant", None)
        if not tenant:
            return get_empty_scope_node_queryset()
        qs = get_scope_nodes_for_tenant(tenant)
        if is_effective_staff(getattr(self.request, "user", None), tenant):
            return qs
        student = get_request_student(self.request)
        if not student:
            return get_empty_scope_node_queryset()
        from apps.domains.enrollment.models import Enrollment

        lecture_ids = Enrollment.objects.filter(
            tenant=tenant,
            student=student,
            status="ACTIVE",
        ).values_list("lecture_id", flat=True)
        return qs.filter(lecture_id__in=lecture_ids)
