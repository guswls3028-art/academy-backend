"""랜딩 메인 KPI band — 게시글 / 후기 / 적중보고서 활동성 신호 (외부 OK)."""
from datetime import timedelta

from django.utils import timezone
from rest_framework import views
from rest_framework.response import Response

from apps.api.common.query_params import parse_query_int
from apps.core.permissions import TenantResolved

from ...models import PublicBoardPost, PublicReview


class PublicCommunityStatsView(views.APIView):
    """GET /landing-public/stats/?days=7
    랜딩 메인 KPI band — "이번 주 후기 N · 게시글 M" 신호.
    """

    permission_classes = [TenantResolved]

    def get(self, request):
        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response({"reviews_week": 0, "board_week": 0, "reviews_total": 0, "board_total": 0, "average_rating": 0.0})
        days = max(
            1,
            min(
                parse_query_int(request.query_params, "days", default=7),
                90,
            ),
        )
        since = timezone.now() - timedelta(days=days)

        reviews_qs = PublicReview.objects.filter(tenant=tenant, status=PublicReview.Status.APPROVED)
        board_qs = PublicBoardPost.objects.filter(
            tenant=tenant, status=PublicBoardPost.Status.PUBLISHED, external_visible=True,
        )

        from django.db.models import Avg
        avg = reviews_qs.aggregate(a=Avg("rating"))["a"] or 0.0

        return Response({
            "reviews_week": reviews_qs.filter(created_at__gte=since).count(),
            "board_week": board_qs.filter(created_at__gte=since).count(),
            "reviews_total": reviews_qs.count(),
            "board_total": board_qs.count(),
            "average_rating": round(float(avg), 2),
            "window_days": days,
        })
