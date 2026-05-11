"""커뮤니티 사용자 알림(#62 N, 2026-05-12).

학생/학부모/staff가 본인 글·댓글에 새 활동(댓글/좋아요) 발생 시 in-app 알림.

설계:
- recipient = Django User FK (Student.user OneToOne으로 학생도 user 보유).
- kind = post_reply / post_like / reply_like / child_reply (답글의 답글)
- payload = JSON으로 post_id/reply_id/actor_user_id/actor_name 저장 (frontend가 deep-link).
- read_at nullable. mark-all-read 일괄.
- tenant 절대 격리.

signal은 별도 파일에서 PostReply/PostLike/PostReplyLike post_save로 trigger.
"""
from django.conf import settings
from django.db import models
from apps.core.models import Tenant


class CommunityNotification(models.Model):
    KIND_POST_REPLY = "post_reply"
    KIND_POST_LIKE = "post_like"
    KIND_REPLY_LIKE = "reply_like"
    KIND_CHILD_REPLY = "child_reply"
    KIND_CHOICES = [
        (KIND_POST_REPLY, "내 글에 댓글"),
        (KIND_POST_LIKE, "내 글에 좋아요"),
        (KIND_REPLY_LIKE, "내 댓글에 좋아요"),
        (KIND_CHILD_REPLY, "내 댓글에 답글"),
    ]

    tenant = models.ForeignKey(
        Tenant, on_delete=models.CASCADE,
        related_name="community_notifications", db_index=True,
    )
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name="community_notifications", db_index=True,
    )
    kind = models.CharField(max_length=20, choices=KIND_CHOICES, db_index=True)
    payload = models.JSONField(default=dict, blank=True)
    read_at = models.DateTimeField(null=True, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "community_notification"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["tenant", "recipient", "-created_at"]),
            models.Index(fields=["recipient", "read_at"]),
        ]

    def __str__(self) -> str:
        return f"Notification(to={self.recipient_id}, kind={self.kind})"
