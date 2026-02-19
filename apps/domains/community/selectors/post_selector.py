from typing import Optional

from django.db.models import Prefetch, QuerySet, Q, Count

from apps.domains.community.models import PostEntity, PostMapping, ScopeNode


def get_empty_post_queryset() -> QuerySet:
    """tenant 없을 때 등 빈 목록용."""
    return PostEntity.objects.none()


def get_post_by_id(tenant, post_id: int):
    """단건 조회. mappings prefetch, replies_count 포함. 없으면 None."""
    return (
        PostEntity.objects.filter(tenant=tenant, id=post_id)
        .annotate(replies_count=Count("replies"))
        .select_related("block_type", "created_by")
        .prefetch_related(
            Prefetch(
                "mappings",
                queryset=PostMapping.objects.select_related("node", "node__lecture", "node__session"),
            )
        )
        .first()
    )


def get_all_posts_for_tenant(tenant) -> QuerySet:
    """tenant 전체 Post 목록 (node_id 없을 때 list용). N+1 방지."""
    return (
        PostEntity.objects.filter(tenant=tenant)
        .select_related("block_type", "created_by")
        .prefetch_related(
            Prefetch(
                "mappings",
                queryset=PostMapping.objects.select_related("node", "node__lecture", "node__session"),
            )
        )
        .order_by("-created_at")
    )


def get_posts_for_node(
    tenant,
    node_id: int,
    *,
    include_inherited: bool = True,
) -> QuerySet:
    """
    SESSION: 해당 SESSION + (include_inherited 시) 상위 COURSE 노드에 매핑된 Post.
    COURSE: 해당 COURSE + (include_inherited 시) 하위 SESSION 노드에 매핑된 Post.
    N+1 방지: select_related / prefetch_related.
    """
    node = (
        ScopeNode.objects.filter(id=node_id, tenant=tenant)
        .select_related("lecture", "session", "parent")
        .first()
    )
    if not node:
        return PostEntity.objects.none()

    if include_inherited:
        if node.level == ScopeNode.Level.SESSION:
            scope_node_ids = [node.id]
            if node.parent_id:
                scope_node_ids.append(node.parent_id)
        else:
            scope_node_ids = list(
                ScopeNode.objects.filter(tenant=tenant)
                .filter(Q(id=node.id) | Q(parent_id=node.id))
                .values_list("id", flat=True)
            )
    else:
        scope_node_ids = [node.id]

    post_ids = (
        PostMapping.objects.filter(node_id__in=scope_node_ids)
        .values_list("post_id", flat=True)
        .distinct()
    )
    return (
        PostEntity.objects.filter(id__in=post_ids, tenant=tenant)
        .select_related("block_type", "created_by")
        .prefetch_related(
            Prefetch(
                "mappings",
                queryset=PostMapping.objects.select_related("node", "node__lecture", "node__session"),
            )
        )
        .order_by("-created_at")
    )


def get_admin_post_list(
    tenant,
    *,
    block_type_id: Optional[int] = None,
    lecture_id: Optional[int] = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[QuerySet, int]:
    """관리자용 목록. 필터: block_type, lecture(해당 강의 노드에 매핑된 것만). 페이지네이션."""
    qs = (
        PostEntity.objects.filter(tenant=tenant)
        .select_related("block_type", "created_by")
        .prefetch_related(
            Prefetch(
                "mappings",
                queryset=PostMapping.objects.select_related("node", "node__lecture", "node__session"),
            )
        )
        .order_by("-created_at")
        .distinct()
    )
    if block_type_id is not None:
        qs = qs.filter(block_type_id=block_type_id)
    if lecture_id is not None:
        node_ids = ScopeNode.objects.filter(tenant=tenant, lecture_id=lecture_id).values_list("id", flat=True)
        qs = qs.filter(mappings__node_id__in=node_ids).distinct()
    total = qs.count()
    offset = (page - 1) * page_size
    return qs[offset : offset + page_size], total
