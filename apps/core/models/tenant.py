# PATH: apps/core/models/tenant.py
from decimal import Decimal

from django.db import models
from django.contrib.postgres.fields import JSONField


class Tenant(models.Model):
    """
    Tenant == Academy
    SaaS 단위의 학원
    """

    name = models.CharField(max_length=255)
    code = models.CharField(max_length=50, unique=True)

    owner_name = models.CharField(max_length=100, blank=True)
    phone = models.CharField(max_length=50, blank=True)
    address = models.CharField(max_length=255, blank=True)

    logo = models.ImageField(
        upload_to="academy/logo/",
        null=True,
        blank=True,
    )

    is_active = models.BooleanField(default=True)

    # ---------- 메시징(알림톡) ----------
    # 학원 개별 카카오 프로필 ID (연동 시 저장)
    kakao_pfid = models.CharField(max_length=100, blank=True, default="")
    # 학원별 SMS 발신번호 (솔라피에 등록·인증된 번호, 예: 01031217466)
    messaging_sender = models.CharField(max_length=20, blank=True, default="")
    # 선불 충전 잔액 (원)
    credit_balance = models.DecimalField(
        max_digits=12, decimal_places=0, default=Decimal("0")
    )
    # 알림톡 기능 활성화 여부
    messaging_is_active = models.BooleanField(default=False)
    # 건당 발송 단가 (원, 학원마다 다르게 책정 가능)
    messaging_base_price = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal("0")
    )

    class Meta:
        app_label = "core"
        verbose_name = "Tenant"
        verbose_name_plural = "Tenants"

    def __str__(self):
        return self.name
