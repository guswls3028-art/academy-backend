# apps/support/messaging/serializers.py
from decimal import Decimal
from rest_framework import serializers

from apps.core.models import Tenant
from apps.support.messaging.models import MessageTemplate, AutoSendConfig


class MessagingInfoSerializer(serializers.ModelSerializer):
    """GET/PATCH 응답: 테넌트 메시징 정보"""
    credit_balance = serializers.DecimalField(
        max_digits=12, decimal_places=0, read_only=True
    )
    # 표시용. 현재 발송 차단 정책에는 미사용(정책은 policy.can_send_sms / resolve_kakao_channel 기준).
    is_active = serializers.BooleanField(source="messaging_is_active", read_only=True)
    base_price = serializers.DecimalField(
        source="messaging_base_price", max_digits=10, decimal_places=2, read_only=True
    )

    # 자체 연동 키 — GET 시 마스킹 처리
    own_solapi_api_key = serializers.SerializerMethodField()
    own_solapi_api_secret = serializers.SerializerMethodField()
    own_ppurio_api_key = serializers.SerializerMethodField()
    own_ppurio_account = serializers.CharField(read_only=True)
    has_own_credentials = serializers.SerializerMethodField()

    class Meta:
        model = Tenant
        fields = [
            "kakao_pfid", "messaging_sender", "credit_balance",
            "is_active", "base_price", "messaging_provider",
            "own_solapi_api_key", "own_solapi_api_secret",
            "own_ppurio_api_key", "own_ppurio_account",
            "has_own_credentials",
        ]
        read_only_fields = ["credit_balance", "is_active", "base_price"]

    @staticmethod
    def _mask(value: str) -> str:
        if not value:
            return ""
        if len(value) <= 4:
            return "****"
        return "****" + value[-4:]

    def get_own_solapi_api_key(self, obj) -> str:
        return self._mask(obj.own_solapi_api_key)

    def get_own_solapi_api_secret(self, obj) -> str:
        return self._mask(obj.own_solapi_api_secret)

    def get_own_ppurio_api_key(self, obj) -> str:
        return self._mask(obj.own_ppurio_api_key)

    def get_has_own_credentials(self, obj) -> bool:
        provider = (obj.messaging_provider or "solapi").strip().lower()
        if provider == "ppurio":
            return bool(obj.own_ppurio_api_key and obj.own_ppurio_account)
        return bool(obj.own_solapi_api_key and obj.own_solapi_api_secret)


class MessagingInfoUpdateSerializer(serializers.Serializer):
    """PATCH 요청: PFID, 발신번호, 공급자, 자체 연동 키 수정 가능"""
    kakao_pfid = serializers.CharField(max_length=100, required=False, allow_blank=True)
    messaging_sender = serializers.CharField(max_length=20, required=False, allow_blank=True)
    messaging_provider = serializers.ChoiceField(
        choices=[("solapi", "솔라피"), ("ppurio", "뿌리오")],
        required=False,
    )
    # 자체 연동 키 (직접 연동 모드)
    own_solapi_api_key = serializers.CharField(max_length=200, required=False, allow_blank=True)
    own_solapi_api_secret = serializers.CharField(max_length=200, required=False, allow_blank=True)
    own_ppurio_api_key = serializers.CharField(max_length=200, required=False, allow_blank=True)
    own_ppurio_account = serializers.CharField(max_length=100, required=False, allow_blank=True)


class VerifySenderRequestSerializer(serializers.Serializer):
    """발신번호 인증 요청"""
    phone_number = serializers.CharField(max_length=20, allow_blank=False)


class ChargeRequestSerializer(serializers.Serializer):
    amount = serializers.DecimalField(max_digits=12, decimal_places=0, min_value=Decimal("1"))


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
    message_body = serializers.CharField()
    message_mode = serializers.CharField()


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
    """메시지 발송 요청: 수신자(학생 ID 또는 직원 ID) + 직접 입력 본문 또는 템플릿 ID"""
    student_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        allow_empty=True,
        required=False,
        default=list,
        help_text="수신 대상 학생 ID 목록 (send_to가 student/parent일 때 사용)",
    )
    staff_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        allow_empty=True,
        required=False,
        default=list,
        help_text="수신 대상 직원 ID 목록 (send_to가 staff일 때 사용)",
    )
    send_to = serializers.ChoiceField(
        choices=[("student", "학생"), ("parent", "학부모"), ("staff", "직원")],
        default="parent",
        help_text="학생/학부모/직원 번호로 보낼지",
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
        send_to = attrs.get("send_to") or "parent"
        student_ids = attrs.get("student_ids") or []
        staff_ids = attrs.get("staff_ids") or []
        if send_to == "staff":
            if not staff_ids:
                raise serializers.ValidationError(
                    {"staff_ids": "직원 수신 시 최소 1명의 직원을 선택해 주세요."}
                )
        else:
            if not student_ids:
                raise serializers.ValidationError(
                    {"student_ids": "학생/학부모 수신 시 최소 1명의 학생을 선택해 주세요."}
                )
        if not attrs.get("template_id") and not (attrs.get("raw_body") or "").strip():
            raise serializers.ValidationError(
                {"raw_body": "직접 입력 본문을 넣거나 템플릿을 선택해 주세요."}
            )
        return attrs


class AutoSendConfigSerializer(serializers.ModelSerializer):
    template_name = serializers.CharField(source="template.name", read_only=True, default="")
    template_solapi_status = serializers.CharField(
        source="template.solapi_status", read_only=True, default=""
    )

    class Meta:
        model = AutoSendConfig
        fields = [
            "id",
            "trigger",
            "template",
            "template_name",
            "template_solapi_status",
            "enabled",
            "message_mode",
            "minutes_before",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class AutoSendConfigUpdateSerializer(serializers.Serializer):
    """PATCH: 개별 config 수정"""
    template_id = serializers.IntegerField(required=False, allow_null=True)
    enabled = serializers.BooleanField(required=False)
    message_mode = serializers.ChoiceField(
        choices=[("sms", "SMS만"), ("alimtalk", "알림톡만"), ("both", "알림톡→SMS폴백")],
        required=False,
    )
    minutes_before = serializers.IntegerField(required=False, allow_null=True, min_value=0)
