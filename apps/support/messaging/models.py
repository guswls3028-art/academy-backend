# apps/support/messaging/models.py
"""
알림톡 발송 로그 · 메시지 템플릿 · 자동발송 설정
"""

from decimal import Decimal

from django.db import models


class NotificationLog(models.Model):
    """
    발송 1건당 1행. 워커가 Solapi 호출 후 성공 시 차감·기록, 실패 시 롤백 후 기록(선택).
    """

    tenant = models.ForeignKey(
        "core.Tenant",
        on_delete=models.CASCADE,
        related_name="notification_logs",
        db_index=True,
    )
    sent_at = models.DateTimeField(auto_now_add=True, db_index=True)
    success = models.BooleanField(default=False)
    amount_deducted = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal("0")
    )
    recipient_summary = models.CharField(max_length=500, blank=True, default="")
    template_summary = models.CharField(max_length=255, blank=True, default="")
    failure_reason = models.CharField(max_length=500, blank=True, default="")

    class Meta:
        app_label = "messaging"
        ordering = ["-sent_at"]
        verbose_name = "Notification log"
        verbose_name_plural = "Notification logs"


class MessageTemplate(models.Model):
    """
    메시지 양식 템플릿 — 테넌트별 저장, 카테고리별 사용처 구분
    - default: 기본(어디서나), 기본 블록만
    - lecture: 강의·차시(세션) 내 학생 선택 발송용
    - clinic: 클리닉 내 학생 선택 발송용
    """
    class Category(models.TextChoices):
        DEFAULT = "default", "기본"
        LECTURE = "lecture", "강의"
        CLINIC = "clinic", "클리닉"

    tenant = models.ForeignKey(
        "core.Tenant",
        on_delete=models.CASCADE,
        related_name="message_templates",
        db_index=True,
    )
    category = models.CharField(
        max_length=20,
        choices=Category.choices,
        default=Category.DEFAULT,
        db_index=True,
    )
    name = models.CharField(max_length=120, help_text="템플릿 이름")
    subject = models.CharField(max_length=200, blank=True, default="", help_text="제목(선택)")
    body = models.TextField(help_text="본문")

    # 솔라피 알림톡 검수 신청 연동
    solapi_template_id = models.CharField(max_length=100, blank=True, default="")
    solapi_status = models.CharField(
        max_length=20,
        blank=True,
        default="",
        choices=[
            ("", "미신청"),
            ("PENDING", "검수 대기"),
            ("APPROVED", "승인"),
            ("REJECTED", "반려"),
        ],
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        app_label = "messaging"
        ordering = ["-updated_at"]
        verbose_name = "Message template"
        verbose_name_plural = "Message templates"


class AutoSendConfig(models.Model):
    """
    자동발송 설정 — 트리거별 사용할 템플릿.
    특정 이벤트 발생 시 해당 템플릿으로 메시지 자동 발송.
    """
    class Trigger(models.TextChoices):
        STUDENT_SIGNUP = "student_signup", "가입 완료"
        CLINIC_REMINDER = "clinic_reminder", "클리닉 알림"
        CLINIC_RESERVATION_CREATED = "clinic_reservation_created", "클리닉 예약 생성"
        CLINIC_RESERVATION_CHANGED = "clinic_reservation_changed", "클리닉 예약 변경"

    tenant = models.ForeignKey(
        "core.Tenant",
        on_delete=models.CASCADE,
        related_name="auto_send_configs",
        db_index=True,
    )
    trigger = models.CharField(
        max_length=60,
        choices=Trigger.choices,
        db_index=True,
    )
    template = models.ForeignKey(
        MessageTemplate,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="auto_send_configs",
    )
    enabled = models.BooleanField(default=False)
    message_mode = models.CharField(
        max_length=20,
        choices=[("sms", "SMS만"), ("alimtalk", "알림톡만"), ("both", "알림톡→SMS폴백")],
        default="sms",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        app_label = "messaging"
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "trigger"],
                name="messaging_autosendconfig_tenant_trigger_unique",
            ),
        ]
        verbose_name = "Auto-send config"
        verbose_name_plural = "Auto-send configs"
