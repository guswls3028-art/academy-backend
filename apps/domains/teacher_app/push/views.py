from django.conf import settings
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import IsPlatformAdmin, TenantResolvedAndStaff

from .models import PushNotificationConfig, PushSubscription
from .serializers import (
    PushNotificationConfigSerializer,
    PushSubscribeSerializer,
    PushUnsubscribeSerializer,
)


class PushSubscribeView(APIView):
    """POST: 인증된 테넌트 스태프의 브라우저 Push 구독 등록."""

    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def post(self, request):
        ser = PushSubscribeSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data

        sub, created = PushSubscription.objects.update_or_create(
            tenant=request.tenant,
            user=request.user,
            endpoint=d["endpoint"],
            defaults={
                "p256dh_key": d["p256dh_key"],
                "auth_key": d["auth_key"],
                "user_agent": d.get("user_agent", ""),
                "app_scope": PushSubscription.AppScope.TEACHER,
                "is_active": True,
            },
        )
        return Response(
            {"id": sub.id, "created": created},
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class PushUnsubscribeView(APIView):
    """POST: 브라우저 Push 구독 해제"""

    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def post(self, request):
        ser = PushUnsubscribeSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        deleted, _ = PushSubscription.objects.filter(
            tenant=request.tenant,
            user=request.user,
            endpoint=ser.validated_data["endpoint"],
            app_scope=PushSubscription.AppScope.TEACHER,
        ).delete()
        return Response({"deleted": deleted})


class VapidPublicKeyView(APIView):
    """GET: VAPID 공개키 반환 (프론트에서 pushManager.subscribe에 사용)"""

    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def get(self, request):
        return Response({"public_key": settings.VAPID_PUBLIC_KEY})


class PlatformPushSubscribeView(APIView):
    """POST: register the current platform console browser subscription."""

    permission_classes = [IsAuthenticated, IsPlatformAdmin]

    def post(self, request):
        ser = PushSubscribeSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        subscription, created = PushSubscription.objects.update_or_create(
            tenant=request.tenant,
            user=request.user,
            endpoint=data["endpoint"],
            defaults={
                "p256dh_key": data["p256dh_key"],
                "auth_key": data["auth_key"],
                "user_agent": data.get("user_agent", ""),
                "app_scope": PushSubscription.AppScope.PLATFORM,
                "is_active": True,
            },
        )
        return Response(
            {"id": subscription.id, "created": created},
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class PlatformPushUnsubscribeView(APIView):
    """POST: remove only the current user's platform console subscription."""

    permission_classes = [IsAuthenticated, IsPlatformAdmin]

    def post(self, request):
        ser = PushUnsubscribeSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        deleted, _ = PushSubscription.objects.filter(
            tenant=request.tenant,
            user=request.user,
            endpoint=ser.validated_data["endpoint"],
            app_scope=PushSubscription.AppScope.PLATFORM,
        ).delete()
        return Response({"deleted": deleted})


class PlatformVapidPublicKeyView(APIView):
    """GET: expose the VAPID public key only to platform administrators."""

    permission_classes = [IsAuthenticated, IsPlatformAdmin]

    def get(self, request):
        return Response({"public_key": settings.VAPID_PUBLIC_KEY})


class PushNotificationConfigView(APIView):
    """GET/PATCH: 선생님별 알림 수신 설정"""

    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def get(self, request):
        config = PushNotificationConfig.objects.filter(
            user=request.user,
            tenant=request.tenant,
        ).first()
        if config is None:
            config = PushNotificationConfig(
                user=request.user,
                tenant=request.tenant,
            )
        return Response(PushNotificationConfigSerializer(config).data)

    def patch(self, request):
        config, _ = PushNotificationConfig.objects.get_or_create(
            user=request.user,
            tenant=request.tenant,
        )
        ser = PushNotificationConfigSerializer(config, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        ser.save()
        return Response(ser.data)
