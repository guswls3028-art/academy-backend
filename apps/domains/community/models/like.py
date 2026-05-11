"""커뮤니티 좋아요 모델 — Post / Reply 각각.

학원장 요청(2026-05-11): 글 상세에 좋아요/주소복사/댓글, 댓글에도 좋아요 + 답글.
nexon dnfm 스타일 reaction.

설계:
- user = Django User FK (학생/학부모/강사/원장 모두 User account 보유 — Student.user OneToOne)
- tenant FK — tenant isolation 절대 원칙
- unique_together (post, user) — 같은 사용자가 같은 글에 좋아요 1회
- toggle 방식: 이미 있으면 삭제(좋아요 취소), 없으면 생성

순수 AddModel 마이그레이션 — 기존 PostEntity/PostReply 무손상.
"""

from django.conf import settings
from django.db import models
from apps.core.models import Tenant
from apps.domains.community.models.post import PostEntity
from apps.domains.community.models.reply import PostReply


class PostLike(models.Model):
    """글 좋아요 — (post, user) 단일."""
    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="post_likes",
        db_index=True,
    )
    post = models.ForeignKey(
        PostEntity,
        on_delete=models.CASCADE,
        related_name="likes",
        db_index=True,
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="community_post_likes",
        db_index=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "community_post_like"
        constraints = [
            models.UniqueConstraint(fields=["post", "user"], name="unique_post_like_per_user"),
        ]
        indexes = [
            models.Index(fields=["tenant", "post"]),
        ]

    def __str__(self) -> str:
        return f"PostLike(post={self.post_id}, user={self.user_id})"


class PostReplyLike(models.Model):
    """댓글 좋아요 — (reply, user) 단일."""
    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="post_reply_likes",
        db_index=True,
    )
    reply = models.ForeignKey(
        PostReply,
        on_delete=models.CASCADE,
        related_name="likes",
        db_index=True,
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="community_reply_likes",
        db_index=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "community_post_reply_like"
        constraints = [
            models.UniqueConstraint(fields=["reply", "user"], name="unique_reply_like_per_user"),
        ]
        indexes = [
            models.Index(fields=["tenant", "reply"]),
        ]

    def __str__(self) -> str:
        return f"PostReplyLike(reply={self.reply_id}, user={self.user_id})"
