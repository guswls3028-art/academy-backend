# apps/support/messaging/serializers.py
from decimal import Decimal

from django.utils import timezone
from rest_framework import serializers

from apps.core.models import Tenant
from apps.domains.messaging.effective_templates import resolve_effective_template_status
from apps.domains.messaging.models import MessageTemplate, AutoSendConfig, ScheduledNotification


class MessagingInfoSerializer(serializers.ModelSerializer):
    """GET/PATCH 응답: 테넌트 메시징 정보"""

    # 자체 연동 키 — GET 시 마스킹 처리
    own_solapi_api_key = serializers.SerializerMethodField()
    own_solapi_api_secret = serializers.SerializerMethodField()
    own_ppurio_api_key = serializers.SerializerMethodField()
    own_ppurio_account = serializers.CharField(read_only=True)
    has_own_credentials = serializers.SerializerMethodField()

    class Meta:
        model = Tenant
        fields = [
            "kakao_pfid", "messaging_sender", "messaging_provider",
            "own_solapi_api_key", "own_solapi_api_secret",
            "own_ppurio_api_key", "own_ppurio_account",
            "has_own_credentials",
        ]

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
    status = serializers.CharField()
    claimed_at = serializers.DateTimeField(allow_null=True)
    amount_deducted = serializers.DecimalField(max_digits=10, decimal_places=2)
    recipient_summary = serializers.CharField()
    template_summary = serializers.CharField()
    failure_reason = serializers.CharField()
    message_body = serializers.CharField()
    message_mode = serializers.CharField()
    source_tenant_id = serializers.IntegerField(allow_null=True)
    target_type = serializers.CharField()
    target_id = serializers.CharField()
    target_name = serializers.CharField()


class MessageTemplateSerializer(serializers.ModelSerializer):
    category = serializers.ChoiceField(
        choices=[*MessageTemplate.Category.choices, ("student", "학생")],
        required=False,
    )
    has_content_var = serializers.SerializerMethodField()

    class Meta:
        model = MessageTemplate
        fields = [
            "id",
            "category",
            "name",
            "subject",
            "body",
            "is_system",
            "is_user_default",
            "solapi_template_id",
            "solapi_status",
            "has_content_var",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "is_system",
            "solapi_template_id",
            "solapi_status",
            "has_content_var",
            "created_at",
            "updated_at",
        ]

    @staticmethod
    def get_has_content_var(obj) -> bool:
        """본문에 #{공지내용} 또는 #{내용} 변수가 있는지 — 자유양식 발송 가능 여부"""
        body = obj.body or ""
        return "#{공지내용}" in body or "#{내용}" in body

    @staticmethod
    def validate_category(value: str) -> str:
        # Frontend blockCategory has "student"; persisted template categories do not.
        if value == "student":
            return MessageTemplate.Category.DEFAULT
        return value


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
        choices=[("alimtalk", "알림톡만")],
        default="alimtalk",
        required=False,
        help_text="alimtalk",
    )
    template_id = serializers.IntegerField(required=False, allow_null=True)
    raw_body = serializers.CharField(required=False, allow_blank=True)
    raw_subject = serializers.CharField(required=False, allow_blank=True, default="")
    scheduled_send_at = serializers.DateTimeField(
        required=False,
        allow_null=True,
        help_text="예약 발송 시각. 비어 있으면 즉시 발송합니다.",
    )
    block_category = serializers.CharField(
        required=False,
        allow_blank=True,
        default="",
        help_text=(
            "frontend 발송 진입점의 블록 카테고리 (grades/attendance/clinic 등). "
            "template_id 누락 또는 t.category 매핑 안 될 때 unified 봉투 fallback 매칭에 사용. "
            "학원장 본문 어떻게 수정해도 봉투(검수 양식)는 유지되어 발송 (domain.md §5)."
        ),
    )
    alimtalk_extra_vars = serializers.DictField(
        child=serializers.CharField(allow_blank=True),
        required=False,
        default=dict,
        help_text="알림톡 추가 치환 변수 (예: {시험명: '수학', 시험성적: '80/100'})",
    )
    alimtalk_extra_vars_per_student = serializers.DictField(
        required=False,
        default=dict,
        help_text="학생별 개별 치환 변수 (key: student_id, value: {변수명: 값})",
    )

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
        if not attrs.get("template_id") and (attrs.get("raw_body") or "").strip():
            if not (attrs.get("block_category") or "").strip():
                raise serializers.ValidationError(
                    {
                        "block_category": (
                            "템플릿 없이 직접 발송할 때는 발송 진입점 카테고리가 필요합니다. "
                            "미리보기/확인 발송 경로를 사용해 주세요."
                        )
                    }
                )
        scheduled_send_at = attrs.get("scheduled_send_at")
        if scheduled_send_at is not None and scheduled_send_at <= timezone.now():
            raise serializers.ValidationError(
                {"scheduled_send_at": "예약 발송 시각은 현재 이후여야 합니다."}
            )
        return attrs


class ScheduledNotificationSerializer(serializers.ModelSerializer):
    recipient_summary = serializers.SerializerMethodField()
    message_preview = serializers.SerializerMethodField()
    target_type = serializers.SerializerMethodField()
    target_id = serializers.SerializerMethodField()
    target_name = serializers.SerializerMethodField()
    message_mode = serializers.SerializerMethodField()

    class Meta:
        model = ScheduledNotification
        fields = [
            "id",
            "trigger",
            "send_at",
            "status",
            "recipient_summary",
            "message_preview",
            "target_type",
            "target_id",
            "target_name",
            "message_mode",
            "created_at",
            "sent_at",
            "error_message",
        ]

    @staticmethod
    def _payload(obj) -> dict:
        return obj.payload if isinstance(obj.payload, dict) else {}

    def get_recipient_summary(self, obj) -> str:
        payload = self._payload(obj)
        target_name = (payload.get("target_name") or "").strip()
        to = (payload.get("to") or "").strip()
        if to and len(to) >= 7:
            to = f"{to[:3]}****{to[-4:]}"
        return " / ".join(part for part in [target_name, to] if part)

    def get_message_preview(self, obj) -> str:
        payload = self._payload(obj)
        text = (payload.get("text") or "").strip()
        return text[:160]

    def get_target_type(self, obj) -> str:
        return (self._payload(obj).get("target_type") or "").strip()

    def get_target_id(self, obj) -> str:
        return str(self._payload(obj).get("target_id") or "")

    def get_target_name(self, obj) -> str:
        return (self._payload(obj).get("target_name") or "").strip()

    def get_message_mode(self, obj) -> str:
        return (self._payload(obj).get("message_mode") or "").strip()


class AutoSendConfigSerializer(serializers.ModelSerializer):
    template_name = serializers.CharField(source="template.name", read_only=True, default="")
    template_subject = serializers.CharField(source="template.subject", read_only=True, default="")
    template_body = serializers.CharField(source="template.body", read_only=True, default="")
    template_solapi_status = serializers.CharField(
        source="template.solapi_status", read_only=True, default=""
    )
    effective_solapi_template_id = serializers.SerializerMethodField()
    effective_template_solapi_status = serializers.SerializerMethodField()
    effective_template_source = serializers.SerializerMethodField()
    effective_template_is_approved = serializers.SerializerMethodField()
    # delay_mode/delay_value — 마이그레이션 전에도 안전하게 동작 (컬럼 미존재 시 기본값)
    delay_mode = serializers.SerializerMethodField()
    delay_value = serializers.SerializerMethodField()

    @staticmethod
    def _effective_status(obj):
        return resolve_effective_template_status(obj)

    def get_effective_solapi_template_id(self, obj) -> str:
        return self._effective_status(obj).solapi_template_id

    def get_effective_template_solapi_status(self, obj) -> str:
        return self._effective_status(obj).solapi_status

    def get_effective_template_source(self, obj) -> str:
        return self._effective_status(obj).source

    def get_effective_template_is_approved(self, obj) -> bool:
        return self._effective_status(obj).is_approved

    def get_delay_mode(self, obj) -> str:
        try:
            return (obj.delay_mode or "immediate") if hasattr(obj, "delay_mode") else "immediate"
        except Exception:
            return "immediate"

    def get_delay_value(self, obj):
        try:
            return obj.delay_value if hasattr(obj, "delay_value") else None
        except Exception:
            return None

    class Meta:
        model = AutoSendConfig
        fields = [
            "id",
            "trigger",
            "template",
            "template_name",
            "template_subject",
            "template_body",
            "template_solapi_status",
            "effective_solapi_template_id",
            "effective_template_solapi_status",
            "effective_template_source",
            "effective_template_is_approved",
            "enabled",
            "message_mode",
            "minutes_before",
            "delay_mode",
            "delay_value",
            "show_actual_time",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]


class AutoSendConfigUpdateSerializer(serializers.Serializer):
    """PATCH: 개별 config 수정"""
    template_id = serializers.IntegerField(required=False, allow_null=True)
    enabled = serializers.BooleanField(required=False)
    message_mode = serializers.ChoiceField(
        choices=[("alimtalk", "알림톡만")],
        required=False,
    )
    minutes_before = serializers.IntegerField(required=False, allow_null=True, min_value=0)
    delay_mode = serializers.ChoiceField(
        choices=[("immediate", "즉시 발송"), ("delay_minutes", "N분 후 발송"), ("scheduled_hour", "지정 시각 발송")],
        required=False,
    )
    delay_value = serializers.IntegerField(required=False, allow_null=True, min_value=0)
    show_actual_time = serializers.BooleanField(required=False)

    def validate(self, attrs):
        delay_mode = attrs.get("delay_mode")
        delay_value = attrs.get("delay_value")
        if delay_mode == "scheduled_hour" and delay_value is not None and delay_value > 23:
            raise serializers.ValidationError(
                {"delay_value": "지정 시각은 0~23 사이여야 합니다."}
            )
        return attrs
