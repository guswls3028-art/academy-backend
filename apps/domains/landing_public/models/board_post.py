from django.db import models

from apps.core.models.base import TimestampModel


class PublicBoardPost(TimestampModel):
    """공개 자유게시판 게시글.

    family-only `community.PostEntity`와 별개 도메인. 본질:
      - 비로그인 외부 학부모도 읽기 가능 (`external_visible=True` + `status=published`)
      - 학원 family(학생/학부모/강사) 작성, 학원장 모더레이션
      - HOT/pinned 뱃지, 익명 toggle, 카테고리 분류
      - matchup 적중보고서 cross-attach (meta.matchup_report_ids)
    """

    class Category(models.TextChoices):
        FREE = "free", "자유"
        TIP = "tip", "공부 팁"
        STORY = "story", "수강 이야기"
        QUESTION = "question", "질문"
        OTHER = "other", "기타"

    class Status(models.TextChoices):
        PUBLISHED = "published", "게시"
        HIDDEN = "hidden", "숨김"
        DELETED = "deleted", "삭제"

    tenant = models.ForeignKey(
        "core.Tenant",
        on_delete=models.CASCADE,
        related_name="public_board_posts",
        db_index=True,
    )
    author = models.ForeignKey(
        "core.User",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="public_board_posts",
    )
    author_display_name = models.CharField(max_length=80, blank=True)
    author_role = models.CharField(
        max_length=20, blank=True,
        help_text="작성 당시 역할(student/parent/teacher/owner/admin)",
    )
    is_anonymous = models.BooleanField(
        default=False,
        help_text="익명 표시(작성자 본인은 자기 글로 인식 가능, 타인에게는 필명 노출)",
    )
    title = models.CharField(max_length=200)
    content = models.TextField()
    category = models.CharField(
        max_length=20, choices=Category.choices, default=Category.FREE, db_index=True,
    )
    cover_image_url = models.URLField(max_length=500, blank=True)

    is_pinned = models.BooleanField(default=False, db_index=True)
    is_hot = models.BooleanField(
        default=False,
        help_text="HOT 뱃지 (좋아요 N개 이상 자동 또는 학원장 수동)",
    )
    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.PUBLISHED, db_index=True,
    )
    external_visible = models.BooleanField(
        default=True, db_index=True,
        help_text="비로그인 외부 학부모 노출 여부(학원장 toggle)",
    )

    view_count = models.PositiveIntegerField(default=0)
    like_count = models.PositiveIntegerField(default=0)
    reply_count = models.PositiveIntegerField(default=0)

    moderated_by = models.ForeignKey(
        "core.User",
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="moderated_public_board_posts",
    )
    moderated_at = models.DateTimeField(null=True, blank=True)

    meta = models.JSONField(
        default=dict, blank=True,
        help_text="확장 필드: matchup_report_ids 등 cross-attach",
    )

    class Meta:
        db_table = "landing_public_board_post"
        ordering = ["-is_pinned", "-created_at"]
        indexes = [
            models.Index(fields=["tenant", "-created_at"]),
            models.Index(fields=["tenant", "status", "external_visible", "-created_at"]),
            models.Index(fields=["tenant", "category", "-created_at"]),
        ]

    def __str__(self):
        return f"PublicBoardPost(tenant={self.tenant_id}, id={self.pk}, title={self.title[:40]})"
