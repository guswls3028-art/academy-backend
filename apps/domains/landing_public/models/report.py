from django.db import models

from apps.core.models.base import TimestampModel


class PublicReport(TimestampModel):
    """공개 컨텐츠(자유게시판/수강후기/댓글)에 대한 신고.

    학원 family 또는 외부 학부모가 부적절한 글/후기/댓글을 신고. 학원장 inbox에서
    pending list 노출 → reviewed(처리) / dismissed(무시). 학원장이 대상 모델을
    함께 hidden/rejected 처리할 수 있도록 detail link 포함.

    auto-hide threshold: 신고 N건 누적 시 자동 숨김 정책은 Phase 4-C inbox 도입 후 결정.
    """

    class TargetKind(models.TextChoices):
        BOARD = "board", "자유게시판"
        REVIEW = "review", "수강후기"
        REPLY = "reply", "댓글"

    class Reason(models.TextChoices):
        SPAM = "spam", "광고/스팸"
        ABUSE = "abuse", "욕설/비방"
        FALSE = "false", "허위/조작"
        COPYRIGHT = "copyright", "저작권 침해"
        PRIVACY = "privacy", "개인정보 노출"
        OTHER = "other", "기타"

    class Status(models.TextChoices):
        PENDING = "pending", "처리대기"
        REVIEWED = "reviewed", "처리완료"
        DISMISSED = "dismissed", "기각"

    tenant = models.ForeignKey(
        "core.Tenant",
        on_delete=models.CASCADE,
        related_name="public_reports",
        db_index=True,
    )
    target_kind = models.CharField(max_length=10, choices=TargetKind.choices)
    target_id = models.PositiveIntegerField(db_index=True)
    reporter = models.ForeignKey(
        "core.User",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="public_reports_filed",
        help_text="신고자(비로그인은 null)",
    )
    reporter_ip = models.GenericIPAddressField(
        null=True, blank=True,
        help_text="비로그인 신고자 IP — 중복 신고/스팸 차단용",
    )
    reason = models.CharField(max_length=20, choices=Reason.choices)
    description = models.TextField(blank=True, help_text="추가 설명(선택)")

    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.PENDING, db_index=True,
    )
    reviewed_by = models.ForeignKey(
        "core.User",
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="public_reports_reviewed",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    action_taken = models.CharField(
        max_length=40, blank=True,
        help_text="학원장이 취한 조치(예: hidden/rejected/dismissed)",
    )

    class Meta:
        db_table = "landing_public_report"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["tenant", "status", "-created_at"]),
            models.Index(fields=["tenant", "target_kind", "target_id"]),
        ]

    def __str__(self):
        return f"PublicReport(tenant={self.tenant_id}, kind={self.target_kind}, target_id={self.target_id}, status={self.status})"


class PublicUserBlock(TimestampModel):
    """학원장이 작성자(학생/학부모/외부 등)를 외부 공개 커뮤니티에서 차단.

    차단된 사용자는 board/review/reply 작성 차단. 단 기존 작성 글은 hidden 처리
    별도 정책(학원장이 detail에서 hide 모더레이션).
    """

    tenant = models.ForeignKey(
        "core.Tenant",
        on_delete=models.CASCADE,
        related_name="public_user_blocks",
        db_index=True,
    )
    blocked_user = models.ForeignKey(
        "core.User",
        on_delete=models.CASCADE,
        related_name="landing_public_blocked_records",
    )
    blocked_by = models.ForeignKey(
        "core.User",
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="landing_public_block_actions",
    )
    reason = models.CharField(max_length=200, blank=True)

    class Meta:
        db_table = "landing_public_user_block"
        unique_together = [("tenant", "blocked_user")]
        indexes = [
            models.Index(fields=["tenant", "blocked_user"]),
        ]

    def __str__(self):
        return f"PublicUserBlock(tenant={self.tenant_id}, user={self.blocked_user_id})"
