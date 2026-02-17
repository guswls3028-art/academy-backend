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
        fields = ["kakao_pfid", "messaging_sender", "credit_balance", "is_active", "base_price"]
        read_only_fields = ["credit_balance", "is_active", "base_price"]


class MessagingInfoUpdateSerializer(serializers.Serializer):
    """PATCH 요청: PFID, 발신번호 수정 가능"""
    kakao_pfid = serializers.CharField(max_length=100, required=False, allow_blank=True)
    messaging_sender = serializers.CharField(max_length=20, required=False, allow_blank=True)


class VerifySenderRequestSerializer(serializers.Serializer):
    """발신번호 인증 요청"""
    phone_number = serializers.CharField(max_length=20, allow_blank=False)


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


class SendMessageRequestSerializer(serializers.Serializer):
    """메시지 발송 요청: 수신자(학생 ID) + 직접 입력 본문 또는 템플릿 ID"""
    student_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        allow_empty=False,
        help_text="수신 대상 학생 ID 목록",
    )
    send_to = serializers.ChoiceField(
        choices=[("student", "학생"), ("parent", "학부모")],
        default="parent",
        help_text="학생 번호로 보낼지 학부모 번호로 보낼지",
    )
    message_mode = serializers.ChoiceField(
        choices=[("sms", "SMS만"), ("alimtalk", "알림톡만"), ("both", "알림톡→SMS폴백")],
        default="sms",
        required=False,
        help_text="sms | alimtalk | both",
    )
    template_id = serializers.IntegerField(required=False, allow_null=True)
    raw_body = serializers.CharField(required=False, allow_blank=True)
    raw_subject = serializers.CharField(required=False, allow_blank=True, default="")

    def validate(self, attrs):
        if not attrs.get("template_id") and not (attrs.get("raw_body") or "").strip():
            raise serializers.ValidationError(
                {"raw_body": "직접 입력 본문을 넣거나 템플릿을 선택해 주세요."}
            )
        return attrs
