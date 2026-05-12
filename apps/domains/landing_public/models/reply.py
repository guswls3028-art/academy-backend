from django.db import models

from apps.core.models.base import TimestampModel


class PublicPostReply(TimestampModel):
    """자유게시판 / 수강후기 공용 댓글.

    target_kind + target_id polymorphic. board / review 모두 댓글 수신.
    학원장 답글은 `is_owner_reply=True` — UI에서 "운영자" 뱃지.
    대댓글은 `parent_reply` self-FK (커뮤니티 SSOT와 동일 패턴).
    """

    class TargetKind(models.TextChoices):
        BOARD = "board", "자유게시판"
        REVIEW = "review", "수강후기"

    tenant = models.ForeignKey(
        "core.Tenant",
        on_delete=models.CASCADE,
        related_name="public_post_replies",
        db_index=True,
    )
    target_kind = models.CharField(max_length=10, choices=TargetKind.choices)
    target_id = models.PositiveIntegerField(db_index=True)

    author = models.ForeignKey(
        "core.User",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="public_post_replies",
    )
    author_display_name = models.CharField(max_length=80, blank=True)
    author_role = models.CharField(max_length=20, blank=True)
    is_anonymous = models.BooleanField(default=False)
    is_owner_reply = models.BooleanField(
        default=False,
        help_text="학원장(owner) 답글 뱃지 — UI에서 강조",
    )

    content = models.TextField()
    parent_reply = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        null=True, blank=True,
        related_name="child_replies",
        db_index=True,
    )

    is_hidden = models.BooleanField(default=False, help_text="학원장 숨김 처리")
    like_count = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "landing_public_post_reply"
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["tenant", "target_kind", "target_id", "created_at"]),
        ]

    def __str__(self):
        return f"PublicReply(tenant={self.tenant_id}, kind={self.target_kind}, target_id={self.target_id})"
