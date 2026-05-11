"""커뮤니티 신고 모델 — Post / Reply 통합.

학원장 요청(2026-05-11) P3: 부적절한 글/댓글 신고. 관리자(staff/admin)가 admin console에서 검토.

설계:
- 단일 모델 ReportEntry. target_type("post"|"reply") + target_id로 polymorphic 식별.
- (FK 대신 target_id+target_type 패턴 — Post/Reply 어느 쪽 삭제되어도 ReportEntry는 남음, audit 목적)
- reporter = Django User FK
- tenant 절대 격리
- unique_together (target_type, target_id, reporter) — 중복 신고 차단

순수 AddModel — 기존 데이터 무손상.
"""

from django.conf import settings
from django.db import models
from apps.core.models import Tenant


class CommunityReport(models.Model):
    """글/댓글 신고. 학원 운영진 검토용 audit."""
    TARGET_POST = "post"
    TARGET_REPLY = "reply"
    TARGET_CHOICES = [(TARGET_POST, "글"), (TARGET_REPLY, "댓글")]

    REASON_SPAM = "spam"
    REASON_OFFENSIVE = "offensive"
    REASON_PERSONAL_INFO = "personal_info"
    REASON_OTHER = "other"
    REASON_CHOICES = [
        (REASON_SPAM, "스팸/광고"),
        (REASON_OFFENSIVE, "욕설/혐오"),
        (REASON_PERSONAL_INFO, "개인정보 노출"),
        (REASON_OTHER, "기타"),
    ]

    STATUS_PENDING = "pending"
    STATUS_RESOLVED = "resolved"
    STATUS_DISMISSED = "dismissed"
    STATUS_CHOICES = [
        (STATUS_PENDING, "대기"),
        (STATUS_RESOLVED, "처리됨"),
        (STATUS_DISMISSED, "기각"),
    ]

    tenant = models.ForeignKey(
        Tenant, on_delete=models.CASCADE,
        related_name="community_reports", db_index=True,
    )
    target_type = models.CharField(max_length=10, choices=TARGET_CHOICES, db_index=True)
    target_id = models.BigIntegerField(db_index=True, help_text="PostEntity.id 또는 PostReply.id (target_type에 따라)")
    reason = models.CharField(max_length=20, choices=REASON_CHOICES, default=REASON_OTHER)
    detail = models.TextField(blank=True, default="", help_text="신고자가 입력한 추가 상세 (선택)")
    reporter = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="community_reports_filed",
    )
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=STATUS_PENDING, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "community_report"
        ordering = ["-created_at"]
        constraints = [
            # 2026-05-11 보안 리뷰 M1: tenant도 unique에 포함(defensive).
            # 현재 PostEntity/PostReply.id가 globally unique BigAutoField라 (target_type+target_id)만으로도
            # cross-tenant 충돌 위험 X지만, 향후 per-tenant ID sequence 도입 가능성 대비.
            models.UniqueConstraint(
                fields=["tenant", "target_type", "target_id", "reporter"],
                name="unique_report_per_target_reporter",
            ),
        ]
        indexes = [
            models.Index(fields=["tenant", "status"]),
            models.Index(fields=["tenant", "target_type", "target_id"]),
        ]

    def __str__(self) -> str:
        return f"Report({self.target_type}#{self.target_id} by user#{self.reporter_id} status={self.status})"
