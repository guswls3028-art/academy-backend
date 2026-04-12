from rest_framework import viewsets

from apps.domains.community.api.serializers import ScopeNodeMinimalSerializer
from apps.domains.community.selectors import (
    get_scope_nodes_for_tenant,
    get_empty_scope_node_queryset,
)
from apps.core.permissions import TenantResolvedAndMember


class ScopeNodeViewSet(viewsets.ReadOnlyModelViewSet):
    """ScopeNode list for tree. Filter by tenant (from request). Pagination disabled so frontend gets full list."""
    serializer_class = ScopeNodeMinimalSerializer
    permission_classes = [TenantResolvedAndMember]
    pagination_class = None

    def get_queryset(self):
        tenant = getattr(self.request, "tenant", None)
        if not tenant:
            return get_empty_scope_node_queryset()
        return get_scope_nodes_for_tenant(tenant)
