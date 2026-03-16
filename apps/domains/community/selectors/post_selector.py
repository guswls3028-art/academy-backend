from typing import Optional

from django.db.models import Prefetch, QuerySet, Q, Count

from apps.domains.community.models import PostEntity, PostMapping, ScopeNode


# 삭제된 학생 게시물 제외 필터: created_by가 NULL(선생님/영구삭제)이거나 활성 학생만 포함
_EXCLUDE_DELETED_AUTHOR = Q(created_by__isnull=True) | Q(created_by__deleted_at__isnull=True)


def get_empty_post_queryset() -> QuerySet:
    """tenant 없을 때 등 빈 목록용."""
    return PostEntity.objects.none()


def get_post_by_id(tenant, post_id: int):
    """단건 조회. mappings prefetch, replies_count 포함. 없으면 None."""
    return (
        PostEntity.objects.filter(tenant=tenant, id=post_id)
        .annotate(replies_count=Count("replies"))
        .select_related("created_by")
        .prefetch_related(
            Prefetch(
                "mappings",
                queryset=PostMapping.objects.select_related("node", "node__lecture", "node__session"),
            ),
            "attachments",
        )
        .first()
    )


def get_all_posts_for_tenant(tenant) -> QuerySet:
    """tenant 전체 Post 목록 (node_id 없을 때 list용). replies_count, N+1 방지."""
    return (
        PostEntity.objects.filter(tenant=tenant)
        .filter(_EXCLUDE_DELETED_AUTHOR)
        .annotate(replies_count=Count("replies"))
        .select_related("created_by")
        .prefetch_related(
            Prefetch(
                "mappings",
                queryset=PostMapping.objects.select_related("node", "node__lecture", "node__session"),
            ),
            "attachments",
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
        .filter(_EXCLUDE_DELETED_AUTHOR)
        .annotate(replies_count=Count("replies"))
        .select_related("created_by")
        .prefetch_related(
            Prefetch(
                "mappings",
                queryset=PostMapping.objects.select_related("node", "node__lecture", "node__session"),
            ),
            "attachments",
        )
        .order_by("-created_at")
    )


def get_admin_post_list(
    tenant,
    *,
    post_type: Optional[str] = None,
    block_type_id: Optional[int] = None,
    lecture_id: Optional[int] = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[QuerySet, int]:
    """관리자용 목록. 필터: post_type, block_type(레거시), lecture. 페이지네이션."""
    qs = (
        PostEntity.objects.filter(tenant=tenant)
        .filter(_EXCLUDE_DELETED_AUTHOR)
        .annotate(replies_count=Count("replies"))
        .select_related("created_by")
        .prefetch_related(
            Prefetch(
                "mappings",
                queryset=PostMapping.objects.select_related("node", "node__lecture", "node__session"),
            ),
            "attachments",
        )
        .order_by("-created_at")
        .distinct()
    )
    if post_type:
        qs = qs.filter(post_type=post_type)
    elif block_type_id is not None:
        # Legacy: filter by block_type FK (backward compat)
        qs = qs.filter(block_type_id=block_type_id)
    if lecture_id is not None:
        node_ids = ScopeNode.objects.filter(tenant=tenant, lecture_id=lecture_id).values_list("id", flat=True)
        qs = qs.filter(mappings__node_id__in=node_ids).distinct()
    total = qs.count()
    offset = (page - 1) * page_size
    return qs[offset : offset + page_size], total


def get_notice_posts_for_tenant(tenant) -> QuerySet:
    """테넌트의 공지 게시물 목록 (post_type='notice'). 학생앱 공지 목록 및 관리자와 동일 데이터."""
    return (
        PostEntity.objects.filter(tenant=tenant, post_type="notice")
        .filter(_EXCLUDE_DELETED_AUTHOR)
        .annotate(replies_count=Count("replies"))
        .select_related("created_by")
        .prefetch_related(
            Prefetch(
                "mappings",
                queryset=PostMapping.objects.select_related("node", "node__lecture", "node__session"),
            ),
            "attachments",
        )
        .order_by("-created_at")
    )
