from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from apps.core.models.base import TimestampModel


class PublicReview(TimestampModel):
    """공개 수강후기.

    학원 family(학생/학부모) 작성 → 학원장 승인(approved) → 외부 공개.
    기존 `core.LandingTestimonialSubmission`(필명+텍스트만)의 본격 확장:
      - 평점 1~5 ★
      - 학년 / 과목 / 수강 개월 (검증 메타)
      - 사진 첨부 (photos JSON list)
      - 검증 뱃지(is_verified) — 등록DB 매칭 또는 학원장 수동 인증
      - 좋아요 / 댓글 카운트 캐시
      - 거절(rejected) / 숨김(hidden) 분리
    레거시 `LandingTestimonialSubmission` 은 hero 영역 간이 후기 풀로 유지(별도 트랙).
    """

    class Status(models.TextChoices):
        PENDING = "pending", "승인대기"
        APPROVED = "approved", "공개"
        REJECTED = "rejected", "거절"
        HIDDEN = "hidden", "숨김"

    tenant = models.ForeignKey(
        "core.Tenant",
        on_delete=models.CASCADE,
        related_name="public_reviews",
        db_index=True,
    )
    author = models.ForeignKey(
        "core.User",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="public_reviews",
    )
    author_display_name = models.CharField(max_length=80, blank=True)
    author_role = models.CharField(
        max_length=20, blank=True,
        help_text="작성 당시 역할(student/parent)",
    )
    is_anonymous = models.BooleanField(default=False)

    rating = models.PositiveSmallIntegerField(
        default=5,
        validators=[MinValueValidator(1), MaxValueValidator(5)],
        help_text="평점 1~5",
    )
    title = models.CharField(max_length=200, blank=True)
    content = models.TextField()

    grade = models.CharField(
        max_length=20, blank=True,
        help_text="학년(예: 고1 / 중2 / N수)",
    )
    subject = models.CharField(
        max_length=40, blank=True,
        help_text="과목(예: 통합과학 / 수학 / 영어)",
    )
    enrollment_months = models.PositiveSmallIntegerField(
        default=0,
        help_text="수강 개월 수(0=미입력)",
    )
    photos = models.JSONField(
        default=list, blank=True,
        help_text="사진 URL 리스트 (max 8 권장)",
    )
    cover_image_url = models.URLField(max_length=500, blank=True)

    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.PENDING, db_index=True,
    )
    is_pinned = models.BooleanField(default=False, db_index=True)
    is_verified = models.BooleanField(
        default=False,
        help_text="수강 이력 검증 (등록DB 매칭 또는 학원장 수동)",
    )

    reviewed_by = models.ForeignKey(
        "core.User",
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="reviewed_public_reviews",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)

    like_count = models.PositiveIntegerField(default=0)
    reply_count = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "landing_public_review"
        ordering = ["-is_pinned", "-created_at"]
        indexes = [
            models.Index(fields=["tenant", "-created_at"]),
            models.Index(fields=["tenant", "status", "-created_at"]),
            models.Index(fields=["tenant", "rating"]),
        ]

    def __str__(self):
        return f"PublicReview(tenant={self.tenant_id}, id={self.pk}, rating={self.rating})"
