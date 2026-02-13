from django.db import models
from apps.core.models import Tenant
from .block_type import BlockType


class PostTemplate(models.Model):
    """자주 쓰는 글 양식. 제목/본문/유형 저장 후 불러와서 재사용."""
    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="post_templates",
        null=False,
        db_index=True,
    )
    name = models.CharField(max_length=128, help_text="양식 이름 (예: 중간고사 공지)")
    block_type = models.ForeignKey(
        BlockType,
        on_delete=models.PROTECT,
        related_name="post_templates",
        null=True,
        blank=True,
        help_text="기본 유형 (선택)",
    )
    title = models.CharField(max_length=255, default="", blank=True)
    content = models.TextField(default="", blank=True)
    order = models.PositiveIntegerField(default=0, help_text="목록 정렬용")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["order", "id"]
        indexes = [
            models.Index(fields=["tenant", "order"]),
        ]

    def __str__(self):
        return self.name
