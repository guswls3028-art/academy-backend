from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.domains.community.api.serializers import (
    PostEntitySerializer,
    PostReplySerializer,
    BlockTypeSerializer,
    ScopeNodeMinimalSerializer,
    PostTemplateSerializer,
)
from apps.domains.community.selectors import (
    get_posts_for_node,
    get_admin_post_list,
    get_post_by_id,
    get_all_posts_for_tenant,
    get_empty_post_queryset,
    get_block_types_for_tenant,
    get_empty_block_type_queryset,
    get_scope_nodes_for_tenant,
    get_empty_scope_node_queryset,
)
from apps.domains.community.services import CommunityService
from apps.domains.community.models import PostTemplate, PostReply, BlockType
from apps.domains.student_app.permissions import get_request_student


class PostViewSet(viewsets.ModelViewSet):
    """Post CRUD. tenant from request. list: ?node_id= or admin list."""
    serializer_class = PostEntitySerializer

    def get_queryset(self):
        tenant = getattr(self.request, "tenant", None)
        if not tenant:
            return get_empty_post_queryset()
        raw = self.request.query_params.get("node_id")
        try:
            node_id = int(raw) if raw not in (None, "") else None
        except (TypeError, ValueError):
            node_id = None
        if node_id is not None:
            return get_posts_for_node(tenant, node_id, include_inherited=True)
        return get_all_posts_for_tenant(tenant)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response({"detail": "tenant required"}, status=status.HTTP_403_FORBIDDEN)
        node_ids = request.data.get("node_ids") or []
        data = {
            "block_type": serializer.validated_data["block_type"],
            "title": serializer.validated_data["title"],
            "content": serializer.validated_data["content"],
            "created_by": serializer.validated_data.get("created_by"),
        }
        svc = CommunityService(tenant)
        post = svc.create_post(data, node_ids)
        return Response(PostEntitySerializer(post).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["patch"], url_path="nodes")
    def update_nodes(self, request, pk=None):
        """PATCH /posts/:id/nodes/ body: { node_ids: [1,2,3] }"""
        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response({"detail": "tenant required"}, status=status.HTTP_403_FORBIDDEN)
        node_ids = request.data.get("node_ids") or []
        svc = CommunityService(tenant)
        svc.update_post_nodes(int(pk), node_ids)
        post = get_post_by_id(tenant, int(pk))
        if not post:
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response(PostEntitySerializer(post).data)

    @action(detail=True, methods=["get", "post"], url_path="replies")
    def replies(self, request, pk=None):
        """GET/POST /posts/:id/replies/ — 답변 목록 조회, 답변 등록(선생/관리자)."""
        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response({"detail": "tenant required"}, status=status.HTTP_403_FORBIDDEN)
        post = get_post_by_id(tenant, int(pk))
        if not post:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

        if request.method == "GET":
            qs = PostReply.objects.filter(post=post, tenant=tenant).select_related("created_by").order_by("created_at")
            serializer = PostReplySerializer(qs, many=True)
            return Response(serializer.data)

        # POST: 답변 등록 (content만 필수)
        serializer = PostReplySerializer(data=request.data, partial=False)
        serializer.is_valid(raise_exception=True)
        reply = serializer.save(post=post)
        return Response(PostReplySerializer(reply).data, status=status.HTTP_201_CREATED)


class AdminPostViewSet(viewsets.ModelViewSet):
    """Admin list with filters. block_type_id, lecture_id, page, page_size."""
    serializer_class = PostEntitySerializer

    def list(self, request, *args, **kwargs):
        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response({"detail": "tenant required"}, status=status.HTTP_403_FORBIDDEN)
        def _int_or_none(val):
            if val is None or val == "":
                return None
            try:
                return int(val)
            except (TypeError, ValueError):
                return None

        block_type_id = _int_or_none(request.query_params.get("block_type_id"))
        lecture_id = _int_or_none(request.query_params.get("lecture_id"))
        try:
            page = int(request.query_params.get("page") or 1)
            page_size = int(request.query_params.get("page_size") or 20)
        except (TypeError, ValueError):
            page, page_size = 1, 20
        qs, total = get_admin_post_list(
            tenant,
            block_type_id=block_type_id,
            lecture_id=lecture_id,
            page=page,
            page_size=page_size,
        )
        serializer = self.get_serializer(qs, many=True)
        return Response({"results": serializer.data, "count": total})

    def get_queryset(self):
        tenant = getattr(self.request, "tenant", None)
        if not tenant:
            return get_empty_post_queryset()
        return get_all_posts_for_tenant(tenant)


class BlockTypeViewSet(viewsets.ModelViewSet):
    """블록 유형 CRUD. 커스텀 유형 생성/수정/삭제. tenant에 하나도 없으면 기본 QnA 유형 자동 생성."""
    serializer_class = BlockTypeSerializer

    def get_queryset(self):
        tenant = getattr(self.request, "tenant", None)
        if not tenant:
            return get_empty_block_type_queryset()
        return get_block_types_for_tenant(tenant)

    def list(self, request, *args, **kwargs):
        """목록 조회 시 tenant에 블록 유형이 없으면 기본 QnA 유형을 한 번만 생성 후 반환."""
        tenant = getattr(request, "tenant", None)
        if tenant and not get_block_types_for_tenant(tenant).exists():
            BlockType.objects.get_or_create(
                tenant=tenant,
                code="qna",
                defaults={"label": "QnA", "order": 1},
            )
        return super().list(request, *args, **kwargs)

    def perform_create(self, serializer):
        tenant = getattr(self.request, "tenant", None)
        if not tenant:
            return
        data = dict(serializer.validated_data)
        code = (data.pop("code", None) or "").strip()[:32]
        if not code:
            import re
            code = re.sub(r"[^a-zA-Z0-9가-힣_]", "_", data.get("label", ""))[:32] or "CUSTOM"
        serializer.save(tenant=tenant, code=code, **data)


class PostTemplateViewSet(viewsets.ModelViewSet):
    """글 양식 CRUD. 자주 쓰는 제목/본문/유형 저장·불러오기."""
    serializer_class = PostTemplateSerializer

    def get_queryset(self):
        tenant = getattr(self.request, "tenant", None)
        if not tenant:
            return PostTemplate.objects.none()
        return (
            PostTemplate.objects.filter(tenant=tenant)
            .select_related("block_type")
            .order_by("order", "id")
        )

    def perform_create(self, serializer):
        tenant = getattr(self.request, "tenant", None)
        if tenant:
            serializer.save(tenant=tenant)


class ScopeNodeViewSet(viewsets.ReadOnlyModelViewSet):
    """ScopeNode list for tree. Filter by tenant (from request)."""
    serializer_class = ScopeNodeMinimalSerializer

    def get_queryset(self):
        tenant = getattr(self.request, "tenant", None)
        if not tenant:
            return get_empty_scope_node_queryset()
        return get_scope_nodes_for_tenant(tenant)
