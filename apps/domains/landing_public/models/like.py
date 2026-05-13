from django.db import models

from apps.core.models.base import TimestampModel


class PublicPostLike(TimestampModel):
    """자유게시판 / 수강후기 / 댓글 좋아요 — 공용 polymorphic.

    unique_together (user, target_kind, target_id) — 중복 방지.
    카운트 캐시는 각 모델 `like_count`에 signal로 갱신.
    """

    class TargetKind(models.TextChoices):
        BOARD = "board", "자유게시판"
        REVIEW = "review", "수강후기"
        REPLY = "reply", "댓글"

    tenant = models.ForeignKey(
        "core.Tenant",
        on_delete=models.CASCADE,
        related_name="public_post_likes",
        db_index=True,
    )
    target_kind = models.CharField(max_length=10, choices=TargetKind.choices)
    target_id = models.PositiveIntegerField()
    user = models.ForeignKey(
        "core.User",
        on_delete=models.CASCADE,
        related_name="public_post_likes",
    )

    class Meta:
        db_table = "landing_public_post_like"
        # tenant 포함 — 같은 user가 다른 학원의 같은 pk를 누를 수 있어야 함
        # (core.md §1 tenant isolation absolute). 이전: ("user","target_kind","target_id") 만 →
        # cross-tenant 시도 시 UNIQUE violation. 2026-05-13 P0 audit fix.
        unique_together = [("tenant", "user", "target_kind", "target_id")]
        indexes = [
            models.Index(fields=["tenant", "target_kind", "target_id"]),
        ]

    def __str__(self):
        return f"PublicLike(user={self.user_id}, kind={self.target_kind}, target_id={self.target_id})"
