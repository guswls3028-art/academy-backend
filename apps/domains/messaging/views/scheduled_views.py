"""
예약 발송 조회/취소 뷰.
"""

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import TenantResolvedAndStaff
from apps.domains.messaging.models import ScheduledNotification
from apps.domains.messaging.permissions import can_send_messages
from apps.domains.messaging.serializers import ScheduledNotificationSerializer
from apps.domains.messaging.security import redact_terminal_delivery_payload


class ScheduledNotificationListView(APIView):
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def get(self, request):
        if not can_send_messages(request, request.tenant):
            return Response(
                {"detail": "예약 발송 조회 권한이 없습니다."},
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            page = max(1, int(request.query_params.get("page", 1)))
            page_size = min(
                100,
                max(1, int(request.query_params.get("page_size", 30))),
            )
        except (TypeError, ValueError):
            return Response(
                {"detail": "page와 page_size는 정수여야 합니다."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        offset = (page - 1) * page_size
        status_filter = (request.query_params.get("status") or "").strip().lower()

        qs = ScheduledNotification.objects.filter(tenant=request.tenant)
        valid_statuses = {choice[0] for choice in ScheduledNotification.Status.choices}
        if status_filter in valid_statuses:
            qs = qs.filter(status=status_filter)
        qs = qs.order_by("send_at", "id")

        count = qs.count()
        items = qs[offset : offset + page_size]
        return Response({
            "results": ScheduledNotificationSerializer(items, many=True).data,
            "count": count,
        })


class ScheduledNotificationCancelView(APIView):
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def post(self, request, pk: int):
        if not can_send_messages(request, request.tenant):
            return Response(
                {"detail": "예약 발송 취소 권한이 없습니다."},
                status=status.HTTP_403_FORBIDDEN,
            )

        notification_qs = ScheduledNotification.objects.filter(
            tenant=request.tenant,
            pk=pk,
        )
        notification = notification_qs.first()
        if not notification:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        updated = notification_qs.filter(
            status=ScheduledNotification.Status.PENDING,
        ).update(
            status=ScheduledNotification.Status.CANCELLED,
            payload=redact_terminal_delivery_payload(
                trigger=notification.trigger,
                payload=notification.payload,
            ),
        )
        if updated != 1:
            return Response(
                {"detail": "대기 중인 예약 발송만 취소할 수 있습니다."},
                status=status.HTTP_409_CONFLICT,
            )

        notification.refresh_from_db()
        return Response(ScheduledNotificationSerializer(notification).data)
