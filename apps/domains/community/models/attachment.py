from django.db import models
from apps.core.models import Tenant
from .post import PostEntity


class PostAttachment(models.Model):
    """게시물 첨부파일 메타데이터. 실제 파일은 R2 Storage 버킷에 저장."""
    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="post_attachments",
        null=False,
        db_index=True,
    )
    post = models.ForeignKey(
        PostEntity,
        on_delete=models.CASCADE,
        related_name="attachments",
    )
    r2_key = models.CharField(max_length=512, unique=True)
    original_name = models.CharField(max_length=255)
    size_bytes = models.BigIntegerField(default=0)
    content_type = models.CharField(max_length=128, default="application/octet-stream")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["tenant", "post"]),
        ]
        verbose_name = "Post Attachment"
        verbose_name_plural = "Post Attachments"

    def __str__(self):
        return f"{self.original_name} ({self.post_id})"
