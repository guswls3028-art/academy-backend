# PATH: apps/core/models/landing_page.py
#
# 선생님별 랜딩페이지 설정 모델.
# Tenant 1:1 — 각 tenant는 최대 1개의 랜딩페이지를 가진다.
# draft_config / published_config JSON 분리로 Draft ↔ Published 관리.

from django.db import models
from apps.core.models.base import TimestampModel


class LandingPage(TimestampModel):
    """
    선생님 도메인 공개 랜딩페이지 설정.

    - tenant: 소유 tenant (1:1)
    - template_key: 적용 템플릿
    - is_published: 게시 여부
    - draft_config: 편집 중인 설정 (JSON)
    - published_config: 게시된 설정 스냅샷 (JSON, is_published=True 일 때만 유효)
    """

    class TemplateKey(models.TextChoices):
        MINIMAL_TUTOR = "minimal_tutor", "Minimal Tutor"
        PREMIUM_DARK = "premium_dark", "Premium Dark"
        ACADEMIC_TRUST = "academic_trust", "Academic Trust"
        PROGRAM_PROMO = "program_promo", "Program Promo"

    tenant = models.OneToOneField(
        "core.Tenant",
        on_delete=models.CASCADE,
        related_name="landing_page",
    )
    template_key = models.CharField(
        max_length=30,
        choices=TemplateKey.choices,
        default=TemplateKey.MINIMAL_TUTOR,
    )
    is_published = models.BooleanField(default=False)
    draft_config = models.JSONField(default=dict, blank=True)
    published_config = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "core_landing_page"

    def __str__(self):
        status = "published" if self.is_published else "draft"
        return f"LandingPage(tenant={self.tenant_id}, {status})"

    def publish(self):
        """draft_config를 published_config로 복사하고 게시."""
        import copy
        self.published_config = copy.deepcopy(self.draft_config)
        self.published_config["template_key"] = self.template_key
        self.is_published = True
        self.save(update_fields=["published_config", "is_published", "updated_at"])

    def unpublish(self):
        """게시 중단. published_config는 보존."""
        self.is_published = False
        self.save(update_fields=["is_published", "updated_at"])
