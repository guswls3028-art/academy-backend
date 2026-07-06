from django.db import models
from apps.core.models import Tenant
from .post import PostEntity


class PostReply(models.Model):
    """PostEntity(예: QNA)에 대한 답변. tenant 필수."""
    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="post_replies",
        null=False,
        db_index=True,
    )
    post = models.ForeignKey(
        PostEntity,
        on_delete=models.CASCADE,
        related_name="replies",
    )
    content = models.TextField()
    created_by = models.ForeignKey(
        "students.Student",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="post_replies",
    )
    author_display_name = models.CharField(
        max_length=100, null=True, blank=True,
        help_text="작성자 표시명 (관리자: staff 이름, 학생: created_by에서 파생)",
    )
    author_role = models.CharField(
        max_length=20, default="staff", blank=True,
        help_text="작성자 역할 (staff/student)",
    )
    parent_reply = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="child_replies",
        db_index=True,
        help_text="답글 — null이면 최상위 댓글, 값 있으면 해당 댓글의 답글 (2026-05-11 도입).",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        verbose_name = "Post Reply"
        verbose_name_plural = "Post Replies"

    def __str__(self):
        return f"Reply to Post#{self.post_id}"
