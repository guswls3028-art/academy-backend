"""hero_carousel post kind용 public posts endpoint (#64 P1, 2026-05-12).

학원장이 hero에 일반 게시글 mix 시 비로그인 외부 학부모에게도 노출.
보안: staff(author_role='staff') 작성 + status='published'만 통과. 학생 글 노출 X.
tenant resolve: request.tenant (subdomain/path 기반).
"""
import logging
from rest_framework import status, views
from rest_framework.response import Response
from apps.core.permissions import TenantResolved

from apps.domains.community.models import PostEntity

logger = logging.getLogger(__name__)


class LandingPublicPostsView(views.APIView):
    """GET /community/landing/public-posts/?ids=1,2,3
    학원장 hero_carousel용 — staff 작성 published 글만.
    """
    permission_classes = [TenantResolved]  # 비로그인 OK, tenant 격리만

    def get(self, request):
        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response({"posts": []})
        raw = (request.query_params.get("ids") or "").strip()
        if not raw:
            return Response({"posts": []})
        try:
            ids = [int(x) for x in raw.split(",") if x.strip()][:20]
        except ValueError:
            return Response({"posts": []})
        if not ids:
            return Response({"posts": []})

        qs = PostEntity.objects.filter(
            tenant=tenant,
            id__in=ids,
            status="published",
            author_role="staff",   # 학생 글 hero 노출 차단
        ).only("id", "title", "post_type", "category_label", "published_at", "created_at")
        items = list(qs)
        # 본문 첫 이미지 추출 (preview용)
        import re
        results = []
        for p in items:
            full = PostEntity.objects.filter(id=p.id).values_list("content", flat=True).first() or ""
            m = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', full)
            results.append({
                "id": p.id,
                "title": p.title,
                "post_type": p.post_type,
                "category_label": p.category_label,
                "published_at": p.published_at.isoformat() if p.published_at else None,
                "created_at": p.created_at.isoformat() if p.created_at else None,
                "preview_image": m.group(1) if m else None,
            })
        return Response({"posts": results})
