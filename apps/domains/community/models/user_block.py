"""커뮤니티 사용자 차단(#49 G2, 2026-05-12).

학원장이 부적절한 사용자를 학원 커뮤니티에서 차단. 차단된 user는:
- 글/댓글 작성 차단 (403)
- 좋아요/신고 등 reaction 차단

기존 작성된 글/댓글은 유지(audit 목적). 차단 해제 시 정상 복귀.

설계:
- (tenant, user) unique — 같은 학원 내 1회 차단.
- reason 텍스트(학원장 메모, 사용자 비공개).
- blocked_by = 차단을 실행한 staff(audit).
- 차단 해제 시 row 삭제 (또는 unblocked_at timestamp 옵션 — 일단 삭제).
"""
from django.conf import settings
from django.db import models
from apps.core.models import Tenant


class CommunityUserBlock(models.Model):
    """tenant 내 사용자 커뮤니티 차단."""
    tenant = models.ForeignKey(
        Tenant, on_delete=models.CASCADE,
        related_name="community_user_blocks", db_index=True,
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name="community_blocks_received",
    )
    blocked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="community_blocks_issued",
    )
    reason = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "community_user_block"
        constraints = [
            models.UniqueConstraint(fields=["tenant", "user"], name="unique_block_per_tenant_user"),
        ]
        indexes = [
            models.Index(fields=["tenant", "user"]),
        ]

    def __str__(self) -> str:
        return f"Block(tenant={self.tenant_id}, user={self.user_id})"
