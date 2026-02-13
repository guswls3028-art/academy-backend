from django.db.models import QuerySet

from apps.domains.community.models import ScopeNode


def get_scope_nodes_for_tenant(tenant) -> QuerySet:
    """tenant별 ScopeNode 전체. 트리 구성용. lecture, session, parent select_related."""
    return (
        ScopeNode.objects.filter(tenant=tenant)
        .select_related("lecture", "session", "parent")
        .order_by("lecture__title", "session__order")
    )


def get_empty_scope_node_queryset() -> QuerySet:
    return ScopeNode.objects.none()
