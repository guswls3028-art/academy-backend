"""커뮤니티 사용자 알림 endpoints + landing public posts (2026-05-12).

GET    /community/notifications/?unread=true&page=1   — 본인 알림 list (최근 60일)
GET    /community/notifications/unread-count/         — 미읽음 카운트 (헤더 종 아이콘용)
POST   /community/notifications/<id>/read/            — 1개 읽음 처리
POST   /community/notifications/mark-all-read/        — 일괄 읽음

학생/학부모/staff 모두 인증된 user면 본인 알림 호출 가능.
"""
from django.utils import timezone
from rest_framework import status, views
from rest_framework.response import Response

from apps.core.permissions import TenantResolvedAndMember
from apps.domains.community.models import CommunityNotification


class CommunityNotificationListView(views.APIView):
    """GET /community/notifications/ — 본인 알림 list."""
    permission_classes = [TenantResolvedAndMember]

    def get(self, request):
        tenant = getattr(request, "tenant", None)
        user = request.user
        if not tenant or not user or not user.is_authenticated:
            return Response({"detail": "tenant + auth required"}, status=status.HTTP_403_FORBIDDEN)
        unread_only = (request.query_params.get("unread") or "").lower() in ("1", "true", "yes")
        try:
            page = max(1, int(request.query_params.get("page") or 1))
            page_size = min(int(request.query_params.get("page_size") or 20), 100)
        except (TypeError, ValueError):
            page, page_size = 1, 20

        qs = CommunityNotification.objects.filter(tenant=tenant, recipient=user)
        if unread_only:
            qs = qs.filter(read_at__isnull=True)
        total = qs.count()
        offset = (page - 1) * page_size
        items = qs[offset : offset + page_size]
        return Response({
            "count": total,
            "results": [{
                "id": n.id,
                "kind": n.kind,
                "kind_label": n.get_kind_display(),
                "payload": n.payload,
                "read": n.read_at is not None,
                "read_at": n.read_at.isoformat() if n.read_at else None,
                "created_at": n.created_at.isoformat() if n.created_at else None,
            } for n in items],
        })


class CommunityNotificationUnreadCountView(views.APIView):
    """GET /community/notifications/unread-count/ — 헤더 종 아이콘 카운트."""
    permission_classes = [TenantResolvedAndMember]

    def get(self, request):
        tenant = getattr(request, "tenant", None)
        user = request.user
        if not tenant or not user or not user.is_authenticated:
            return Response({"count": 0})
        c = CommunityNotification.objects.filter(tenant=tenant, recipient=user, read_at__isnull=True).count()
        return Response({"count": c})


class CommunityNotificationReadView(views.APIView):
    """POST /community/notifications/<id>/read/ — 1개 읽음 처리."""
    permission_classes = [TenantResolvedAndMember]

    def post(self, request, pk=None):
        tenant = getattr(request, "tenant", None)
        user = request.user
        if not tenant or not user or not user.is_authenticated:
            return Response({"detail": "auth required"}, status=status.HTTP_403_FORBIDDEN)
        try:
            n = CommunityNotification.objects.get(tenant=tenant, recipient=user, id=int(pk))
        except (CommunityNotification.DoesNotExist, ValueError, TypeError):
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        if n.read_at is None:
            n.read_at = timezone.now()
            n.save(update_fields=["read_at"])
        return Response({"id": n.id, "read": True})


class CommunityNotificationMarkAllReadView(views.APIView):
    """POST /community/notifications/mark-all-read/ — 본인 미읽음 일괄."""
    permission_classes = [TenantResolvedAndMember]

    def post(self, request):
        tenant = getattr(request, "tenant", None)
        user = request.user
        if not tenant or not user or not user.is_authenticated:
            return Response({"detail": "auth required"}, status=status.HTTP_403_FORBIDDEN)
        now = timezone.now()
        updated = CommunityNotification.objects.filter(
            tenant=tenant, recipient=user, read_at__isnull=True
        ).update(read_at=now)
        return Response({"updated": updated})
