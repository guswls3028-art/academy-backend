# apps/support/messaging/views_notification.py
"""
수동 알림 발송 API — preview → confirm 2단계.

자동 트리거가 아닌 선생의 명시적 발송만 지원.
preview 없이 confirm 직접 호출 불가.
"""

import logging

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status as http_status
from rest_framework.permissions import IsAuthenticated
from apps.core.permissions import TenantResolvedAndStaff

from apps.support.messaging.notification_dispatch import (
    build_attendance_preview,
    create_preview_token,
    consume_preview_token,
    execute_notification_batch,
)

logger = logging.getLogger(__name__)


class AttendanceNotificationPreviewView(APIView):
    """
    출결 알림 미리보기.

    POST /api/v1/messaging/attendance-notification/preview/
    {
        "session_id": int,
        "notification_type": "check_in" | "absent",
        "send_to": "parent" | "student"  (기본: "parent")
    }

    Returns:
    {
        "preview_token": "uuid",
        "recipients": [...],
        "total_count": int,
        "excluded_count": int,
        "message_preview": str,
        "session_title": str,
        "lecture_title": str,
    }
    """
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def post(self, request):
        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response({"detail": "테넌트 정보가 없습니다."}, status=http_status.HTTP_400_BAD_REQUEST)

        session_id = request.data.get("session_id")
        notification_type = request.data.get("notification_type")  # "check_in" | "absent"
        send_to = request.data.get("send_to", "parent")

        if not session_id or not notification_type:
            return Response(
                {"detail": "session_id와 notification_type은 필수입니다."},
                status=http_status.HTTP_400_BAD_REQUEST,
            )

        if notification_type not in ("check_in", "absent"):
            return Response(
                {"detail": "notification_type은 'check_in' 또는 'absent'만 가능합니다."},
                status=http_status.HTTP_400_BAD_REQUEST,
            )

        preview = build_attendance_preview(tenant, int(session_id), notification_type, send_to)

        if "error" in preview:
            return Response({"detail": preview["error"]}, status=http_status.HTTP_400_BAD_REQUEST)

        if preview["total_count"] == 0:
            # 발송 대상 없으면 토큰 미발급
            return Response({
                "preview_token": None,
                "recipients": preview["recipients"],
                "total_count": 0,
                "excluded_count": preview["excluded_count"],
                "message_preview": preview.get("message_template_body", ""),
                "session_title": preview.get("session_title", ""),
                "lecture_title": preview.get("lecture_title", ""),
            })

        staff_id = getattr(getattr(request, "user", None), "staff_profile_id", None)
        token = create_preview_token(
            tenant=tenant,
            preview_data=preview,
            staff_id=staff_id,
            session_type="attendance",
            session_id=int(session_id),
            notification_type=notification_type,
            send_to=send_to,
        )

        # phone_raw 제거 (프론트에 노출 불필요)
        safe_recipients = []
        for r in preview["recipients"]:
            safe_r = {k: v for k, v in r.items() if k not in ("phone_raw", "alimtalk_replacements")}
            safe_recipients.append(safe_r)

        return Response({
            "preview_token": token,
            "recipients": safe_recipients,
            "total_count": preview["total_count"],
            "excluded_count": preview["excluded_count"],
            "message_preview": preview.get("message_template_body", ""),
            "session_title": preview.get("session_title", ""),
            "lecture_title": preview.get("lecture_title", ""),
        })


class AttendanceNotificationConfirmView(APIView):
    """
    출결 알림 확정 발송.

    POST /api/v1/messaging/attendance-notification/confirm/
    {
        "preview_token": "uuid"
    }

    preview_token 없이 직접 호출 불가.
    동일 token 재사용 불가 (1회용).
    """
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def post(self, request):
        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response({"detail": "테넌트 정보가 없습니다."}, status=http_status.HTTP_400_BAD_REQUEST)

        preview_token = request.data.get("preview_token")
        if not preview_token:
            return Response(
                {"detail": "preview_token은 필수입니다. 미리보기 없이 직접 발송할 수 없습니다."},
                status=http_status.HTTP_400_BAD_REQUEST,
            )

        result = consume_preview_token(preview_token, tenant)
        if "error" in result:
            return Response({"detail": result["error"]}, status=http_status.HTTP_400_BAD_REQUEST)

        batch_result = execute_notification_batch(
            tenant=tenant,
            payload=result["payload"],
            batch_id=result["batch_id"],
            staff_id=result.get("staff_id"),
        )

        return Response({
            "batch_id": batch_result["batch_id"],
            "sent_count": batch_result["sent_count"],
            "failed_count": batch_result.get("failed_count", 0),
            "blocked_count": batch_result.get("blocked_count", 0),
        })
