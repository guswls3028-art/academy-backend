from rest_framework import viewsets, status
from rest_framework.response import Response

from apps.domains.community.api.serializers import PostEntitySerializer
from apps.domains.community.selectors import (
    get_admin_post_list,
    get_all_posts_for_tenant,
    get_empty_post_queryset,
)
from apps.core.permissions import TenantResolvedAndStaff


class AdminPostViewSet(viewsets.GenericViewSet):
    """Admin list with filters. post_type, lecture_id, q, page, page_size."""
    permission_classes = [TenantResolvedAndStaff]
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

        post_type = (request.query_params.get("post_type") or "").strip().lower() or None
        lecture_id = _int_or_none(request.query_params.get("lecture_id"))
        q = (request.query_params.get("q") or "").strip() or None
        try:
            page = int(request.query_params.get("page") or 1)
            page_size = int(request.query_params.get("page_size") or 20)
        except (TypeError, ValueError):
            page, page_size = 1, 20
        qs, total = get_admin_post_list(
            tenant,
            post_type=post_type,
            lecture_id=lecture_id,
            q=q,
            page=page,
            page_size=page_size,
        )
        serializer = self.get_serializer(qs, many=True)
        return Response({"results": serializer.data, "count": total})

    def get_queryset(self):
        tenant = getattr(self.request, "tenant", None)
        if not tenant:
            return get_empty_post_queryset()
        return get_all_posts_for_tenant(tenant, include_unpublished=True)
