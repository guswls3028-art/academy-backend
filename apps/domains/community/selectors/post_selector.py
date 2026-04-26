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
        .annotate(replies_count=Count("replies", distinct=True))
        .select_related("created_by", "block_type")
        .prefetch_related(
            Prefetch(
                "mappings",
                queryset=PostMapping.objects.select_related("node", "node__lecture", "node__session"),
            ),
            "attachments",
        )
        .first()
    )


def get_all_posts_for_tenant(tenant, *, include_unpublished: bool = False) -> QuerySet:
    """tenant 전체 Post 목록 (node_id 없을 때 list용). replies_count, N+1 방지."""
    qs = PostEntity.objects.filter(tenant=tenant)
    if not include_unpublished:
        qs = qs.filter(status="published")
    return (
        qs
        .filter(_EXCLUDE_DELETED_AUTHOR)
        .annotate(replies_count=Count("replies", distinct=True))
        .select_related("created_by", "block_type")
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
    include_unpublished: bool = False,
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
    qs = PostEntity.objects.filter(id__in=post_ids, tenant=tenant)
    if not include_unpublished:
        qs = qs.filter(status="published")
    return (
        qs
        .filter(_EXCLUDE_DELETED_AUTHOR)
        .annotate(replies_count=Count("replies", distinct=True))
        .select_related("created_by", "block_type")
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
    q: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[QuerySet, int]:
    """관리자용 목록. 필터: post_type, block_type(레거시), lecture, q(서버 검색). 페이지네이션."""
    qs = (
        PostEntity.objects.filter(tenant=tenant)
        .filter(_EXCLUDE_DELETED_AUTHOR)
        .annotate(replies_count=Count("replies", distinct=True))
        .select_related("created_by", "block_type")
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
    if q:
        q = q.strip()[:100]
        if q:
            qs = qs.filter(
                Q(title__icontains=q)
                | Q(content__icontains=q)
                | Q(author_display_name__icontains=q)
                | Q(category_label__icontains=q)
            ).distinct()
    total = qs.count()
    offset = (page - 1) * page_size
    return qs[offset : offset + page_size], total


def get_posts_by_type_for_tenant(tenant, post_type: str, *, include_unpublished: bool = False) -> QuerySet:
    """테넌트의 특정 post_type 게시물 목록. 학생앱 공용."""
    qs = PostEntity.objects.filter(tenant=tenant, post_type=post_type)
    if not include_unpublished:
        qs = qs.filter(status="published")
    return (
        qs
        .filter(_EXCLUDE_DELETED_AUTHOR)
        .annotate(replies_count=Count("replies", distinct=True))
        .select_related("created_by", "block_type")
        .prefetch_related(
            Prefetch(
                "mappings",
                queryset=PostMapping.objects.select_related("node", "node__lecture", "node__session"),
            ),
            "attachments",
        )
        .order_by("-created_at")
    )


def get_post_counts_by_node(tenant, post_type: str) -> dict:
    """post_type별 트리 카운트 — 클라이언트가 500건 페치할 필요 없게 단일 집계.

    반환:
      {
        "total": 전체 글 수,
        "by_node_id": {scope_node_id: count},
        "by_lecture_id": {lecture_id: distinct post count},
        "global_count": mapping 없는(전체 대상) 글 수,
      }
    """
    qs = (
        PostEntity.objects.filter(tenant=tenant, post_type=post_type, status="published")
        .filter(_EXCLUDE_DELETED_AUTHOR)
    )
    total = qs.count()

    # mapping 없는 글 (전체 대상)
    global_count = qs.filter(mappings__isnull=True).distinct().count()

    # node별 카운트
    node_counts = (
        PostMapping.objects.filter(
            post__in=qs,
            post__tenant=tenant,
        )
        .values("node_id")
        .annotate(c=Count("post_id", distinct=True))
    )
    by_node_id = {row["node_id"]: row["c"] for row in node_counts}

    # lecture별 distinct post 카운트
    lecture_counts = (
        PostMapping.objects.filter(
            post__in=qs,
            post__tenant=tenant,
        )
        .values("node__lecture_id")
        .annotate(c=Count("post_id", distinct=True))
    )
    by_lecture_id = {
        row["node__lecture_id"]: row["c"]
        for row in lecture_counts
        if row["node__lecture_id"] is not None
    }

    return {
        "total": total,
        "by_node_id": by_node_id,
        "by_lecture_id": by_lecture_id,
        "global_count": global_count,
    }


def get_notice_posts_for_tenant(tenant, *, include_unpublished: bool = False) -> QuerySet:
    """테넌트의 공지 게시물 목록 (post_type='notice'). 학생앱 공지 목록 및 관리자와 동일 데이터."""
    qs = PostEntity.objects.filter(tenant=tenant, post_type="notice")
    if not include_unpublished:
        qs = qs.filter(status="published")
    return (
        qs
        .filter(_EXCLUDE_DELETED_AUTHOR)
        .annotate(replies_count=Count("replies", distinct=True))
        .select_related("created_by", "block_type")
        .prefetch_related(
            Prefetch(
                "mappings",
                queryset=PostMapping.objects.select_related("node", "node__lecture", "node__session"),
            ),
            "attachments",
        )
        .order_by("-created_at")
    )
