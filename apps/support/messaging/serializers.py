# apps/support/messaging/serializers.py
from rest_framework import serializers

from apps.core.models import Tenant
from apps.support.messaging.models import MessageTemplate


class MessagingInfoSerializer(serializers.ModelSerializer):
    """GET/PATCH 응답: 테넌트 메시징 정보"""
    credit_balance = serializers.DecimalField(
        max_digits=12, decimal_places=0, read_only=True
    )
    is_active = serializers.BooleanField(source="messaging_is_active", read_only=True)
    base_price = serializers.DecimalField(
        source="messaging_base_price", max_digits=10, decimal_places=2, read_only=True
    )

    class Meta:
        model = Tenant
        fields = ["kakao_pfid", "credit_balance", "is_active", "base_price"]
        read_only_fields = ["credit_balance", "is_active", "base_price"]


class MessagingInfoUpdateSerializer(serializers.Serializer):
    """PATCH 요청: PFID만 수정 가능"""
    kakao_pfid = serializers.CharField(max_length=100, required=False, allow_blank=True)


class ChargeRequestSerializer(serializers.Serializer):
    amount = serializers.DecimalField(max_digits=12, decimal_places=0, min_value=1)


class ChargeResponseSerializer(serializers.Serializer):
    credit_balance = serializers.DecimalField(max_digits=12, decimal_places=0)


class NotificationLogSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    sent_at = serializers.DateTimeField()
    success = serializers.BooleanField()
    amount_deducted = serializers.DecimalField(max_digits=10, decimal_places=2)
    recipient_summary = serializers.CharField()
    template_summary = serializers.CharField()
    failure_reason = serializers.CharField()


class MessageTemplateSerializer(serializers.ModelSerializer):
    class Meta:
        model = MessageTemplate
        fields = [
            "id",
            "category",
            "name",
            "subject",
            "body",
            "solapi_template_id",
            "solapi_status",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "solapi_template_id",
            "solapi_status",
            "created_at",
            "updated_at",
        ]
