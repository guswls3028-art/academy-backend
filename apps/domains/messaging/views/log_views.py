# apps/support/messaging/views/log_views.py
"""
발송 로그 뷰
"""

from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.db.models import Q

from apps.core.permissions import TenantResolvedAndStaff
from apps.domains.messaging.models import NotificationLog


class NotificationLogListView(APIView):
    """GET: 발송 로그 목록 (페이지네이션)"""
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def get(self, request):
        page = max(1, int(request.query_params.get("page", 1)))
        page_size = min(50, max(1, int(request.query_params.get("page_size", 20))))
        offset = (page - 1) * page_size
        # status 필터: success / failure / all (기본 all)
        status_filter = (request.query_params.get("status") or "").strip().lower()
        base_qs = NotificationLog.objects.filter(
            Q(tenant=request.tenant) | Q(source_tenant=request.tenant)
        )
        if status_filter == "success":
            base_qs = base_qs.filter(success=True)
        elif status_filter == "failure":
            base_qs = base_qs.filter(success=False)
        qs = base_qs.order_by("-sent_at")[offset : offset + page_size]
        count = base_qs.count()
        items = [
            {
                "id": r.id,
                "sent_at": r.sent_at,
                "success": r.success,
                "status": r.status or "",
                "claimed_at": r.claimed_at,
                "amount_deducted": r.amount_deducted,
                "recipient_summary": r.recipient_summary or "",
                "template_summary": r.template_summary or "",
                "provider_message_id": r.provider_message_id or "",
                "failure_reason": r.failure_reason or "",
                "message_body": r.message_body or "",
                "message_mode": r.message_mode or "",
                "notification_type": r.notification_type or "",
                "source_tenant_id": r.source_tenant_id,
                "target_type": r.target_type or "",
                "target_id": r.target_id or "",
                "target_name": r.target_name or "",
            }
            for r in qs
        ]
        return Response({"results": items, "count": count})


class NotificationLogDetailView(APIView):
    """GET: 발송 로그 단건 상세"""
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def get(self, request, pk):
        log = NotificationLog.objects.filter(
            Q(tenant=request.tenant) | Q(source_tenant=request.tenant),
            pk=pk,
        ).first()
        if not log:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response({
            "id": log.id,
            "sent_at": log.sent_at,
            "success": log.success,
            "status": log.status or "",
            "claimed_at": log.claimed_at,
            "amount_deducted": log.amount_deducted,
            "recipient_summary": log.recipient_summary or "",
            "template_summary": log.template_summary or "",
            "provider_message_id": log.provider_message_id or "",
            "failure_reason": log.failure_reason or "",
            "message_body": log.message_body or "",
            "message_mode": log.message_mode or "",
            "notification_type": log.notification_type or "",
            "source_tenant_id": log.source_tenant_id,
            "target_type": log.target_type or "",
            "target_id": log.target_id or "",
            "target_name": log.target_name or "",
        })
