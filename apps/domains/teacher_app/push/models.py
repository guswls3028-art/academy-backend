from django.conf import settings
from django.db import models

from apps.api.common.models import TimestampModel
from apps.core.db import TenantQuerySet
from apps.core.models import Tenant


class PushSubscription(TimestampModel):
    """Web Push 구독 정보 — 선생님 브라우저/PWA별 1건"""

    objects = TenantQuerySet.as_manager()

    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="push_subscriptions",
        db_index=True,
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="push_subscriptions",
    )
    endpoint = models.URLField(max_length=500)
    p256dh_key = models.CharField(max_length=200)
    auth_key = models.CharField(max_length=200)
    user_agent = models.CharField(max_length=300, blank=True, default="")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["tenant", "user"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "endpoint"],
                name="uniq_push_sub_user_endpoint",
            ),
        ]

    def __str__(self):
        return f"PushSub({self.user_id}, active={self.is_active})"


class PushNotificationConfig(TimestampModel):
    """선생님별 푸시 알림 수신 설정"""

    objects = TenantQuerySet.as_manager()

    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="push_notification_configs",
        db_index=True,
    )
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="push_notification_config",
    )
    student_registration = models.BooleanField(default=True)
    qna_new_question = models.BooleanField(default=True)
    exam_submission = models.BooleanField(default=True)
    clinic_booking = models.BooleanField(default=False)
    video_encoding_complete = models.BooleanField(default=True)

    class Meta:
        indexes = [
            models.Index(fields=["tenant", "user"]),
        ]

    def __str__(self):
        return f"PushConfig({self.user_id})"
