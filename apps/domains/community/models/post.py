from django.db import models
from apps.core.models import Tenant
from apps.domains.students.models import Student
from .block_type import BlockType


class PostEntity(models.Model):
    """콘텐츠 단일 객체. 노출 위치는 PostMapping으로 관리. tenant 필수."""
    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="post_entities",
        null=False,
        db_index=True,
    )
    block_type = models.ForeignKey(
        BlockType,
        on_delete=models.PROTECT,
        related_name="posts",
    )
    title = models.CharField(max_length=255)
    content = models.TextField()
    created_by = models.ForeignKey(
        Student,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="post_entities",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["tenant", "created_at"]),
            models.Index(fields=["tenant", "block_type"]),
        ]
        verbose_name = "Post"
        verbose_name_plural = "Posts"

    def __str__(self):
        return self.title
