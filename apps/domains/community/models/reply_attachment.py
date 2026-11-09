from django.db import models


class PostReplyAttachment(models.Model):
    """Private R2 image owned by a Q&A reply."""

    reply = models.ForeignKey(
        "community.PostReply", on_delete=models.CASCADE, related_name="attachments",
    )
    r2_key = models.CharField(max_length=512, unique=True)
    original_name = models.CharField(max_length=255)
    size_bytes = models.BigIntegerField()
    content_type = models.CharField(max_length=128)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]
