# apps/support/messaging/views/log_views.py
"""
발송 로그 뷰
"""

from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from apps.core.permissions import TenantResolvedAndStaff
from apps.support.messaging.models import NotificationLog


class NotificationLogListView(APIView):
    """GET: 발송 로그 목록 (페이지네이션)"""
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def get(self, request):
        page = max(1, int(request.query_params.get("page", 1)))
        page_size = min(50, max(1, int(request.query_params.get("page_size", 20))))
        offset = (page - 1) * page_size
        qs = (
            NotificationLog.objects.filter(tenant=request.tenant)
            .order_by("-sent_at")[offset : offset + page_size]
        )
        count = NotificationLog.objects.filter(tenant=request.tenant).count()
        items = [
            {
                "id": r.id,
                "sent_at": r.sent_at,
                "success": r.success,
                "amount_deducted": r.amount_deducted,
                "recipient_summary": r.recipient_summary or "",
                "template_summary": r.template_summary or "",
                "failure_reason": r.failure_reason or "",
                "message_body": r.message_body or "",
                "message_mode": r.message_mode or "",
            }
            for r in qs
        ]
        return Response({"results": items, "count": count})


class NotificationLogDetailView(APIView):
    """GET: 발송 로그 단건 상세"""
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def get(self, request, pk):
        log = NotificationLog.objects.filter(tenant=request.tenant, pk=pk).first()
        if not log:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response({
            "id": log.id,
            "sent_at": log.sent_at,
            "success": log.success,
            "amount_deducted": log.amount_deducted,
            "recipient_summary": log.recipient_summary or "",
            "template_summary": log.template_summary or "",
            "failure_reason": log.failure_reason or "",
            "message_body": log.message_body or "",
            "message_mode": log.message_mode or "",
        })
