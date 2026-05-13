"""공개 매치업 적중보고서 게시판 — 학원장이 작성 완료된 적중보고서를 게시물 형태로 박는다 (Phase #69).

본질: 박철T가 카페에 PDF로 올리던 흐름을 우리 게시판으로 흡수.
  - 적중보고서 PDF는 generate_curated_hit_report_pdf로 이미 만들어짐
  - 게시 시점에 그 PDF를 R2 storage 버킷에 별도 key로 copy → 게시물에는 그 key만 보관
  - 이후 원본 MatchupHitReport / MatchupDocument 변동되어도 게시물 PDF는 박힌 그대로 (스냅샷 immutable)
  - 매번 PDF 재생성 X — 정적 fetch (느림 회피)

가시성:
  - status PUBLISHED + 현재 시각 ∈ [published_at, published_until] 시에만 일반인 노출
  - 기간 밖이면 owner/admin만 preview (학원장은 항상 자기 게시물 봄)
  - HIDDEN: 일시 비공개. EXPIRED: published_until past — 카드만 노출 / 상세 차단

학원장 작성 데이터 immutable 정책 ([[project_matchup_immutable_policy_2026_05_06]]):
  - MatchupHitReport / MatchupHitReportEntry 자체는 절대 변경 X (READ ONLY)
  - 본 모델은 별도 테이블 — 학원장이 추가/수정/삭제 자유 (자기 게시물에 한해)
"""
from django.db import models

from apps.core.models.base import TimestampModel


class PublicMatchupShowcase(TimestampModel):
    """공개 매치업 적중보고서 showcase (게시물 형태)."""

    class Status(models.TextChoices):
        DRAFT = "draft", "초안"
        PUBLISHED = "published", "공개"
        EXPIRED = "expired", "기간 만료 (카드만 노출)"
        HIDDEN = "hidden", "비공개"

    tenant = models.ForeignKey(
        "core.Tenant",
        on_delete=models.CASCADE,
        related_name="public_matchup_showcases",
        db_index=True,
    )
    # 원본 적중보고서 FK (참조용 — hit_report 삭제되어도 snapshot 유지).
    # MatchupHitReport 자체는 immutable이지만 운영 cleanup으로 삭제될 가능성에 대비.
    hit_report_id_ref = models.PositiveIntegerField(
        null=True, blank=True, db_index=True,
        help_text="원본 MatchupHitReport.id (참조용, FK 아님)",
    )

    # 학원장 입력
    title = models.CharField(
        max_length=200,
        help_text="게시물 제목 (학원장 자유 입력, 기본=원본 보고서 제목)",
    )
    description = models.TextField(
        blank=True, default="",
        help_text="학원장 코멘트 (선택)",
    )

    # 가시성
    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.DRAFT, db_index=True,
    )
    published_at = models.DateTimeField(
        null=True, blank=True, db_index=True,
        help_text="공개 시작 시각. null=즉시 공개 (PUBLISHED 진입 시 now() backfill).",
    )
    published_until = models.DateTimeField(
        null=True, blank=True, db_index=True,
        help_text="공개 종료 시각. past 시 외부엔 카드 요약만 노출. null=무기한.",
    )

    # snapshot — immutable 데이터 (게시 시 backfill, 이후 변경 X)
    # R2 storage 버킷 key. PDF copy 한 번 + 이후 정적 fetch.
    snapshot_pdf_key = models.CharField(max_length=512, blank=True, default="")
    snapshot_pdf_bytes = models.PositiveIntegerField(default=0)
    # meta: { hit_count, exam_count, candidates_total, category, document_title, author_name, snapshot_at_iso }
    snapshot_meta = models.JSONField(default=dict, blank=True)
    snapshot_at = models.DateTimeField(null=True, blank=True, db_index=True)

    # 작성자 (학원장 owner/admin/teacher)
    created_by = models.ForeignKey(
        "core.User",
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="created_public_matchup_showcases",
    )

    # 외부 view count
    view_count = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "landing_public_matchup_showcase"
        ordering = ["-published_at", "-created_at"]
        indexes = [
            models.Index(fields=["tenant", "status", "-published_at"]),
            models.Index(fields=["tenant", "hit_report_id_ref"]),
        ]

    def __str__(self):
        return f"PublicMatchupShowcase(tenant={self.tenant_id}, id={self.pk}, title={self.title[:40]})"

    def is_publicly_visible(self) -> bool:
        """현재 시각 기준 일반 외부인이 상세까지 볼 수 있는가."""
        from django.utils import timezone
        if self.status != self.Status.PUBLISHED:
            return False
        now = timezone.now()
        if self.published_at and now < self.published_at:
            return False
        if self.published_until and now > self.published_until:
            return False
        return True

    def is_card_only(self) -> bool:
        """카드 요약만 노출 (상세 차단). 만료 / HIDDEN / DRAFT."""
        from django.utils import timezone
        if self.status == self.Status.EXPIRED:
            return True
        if self.status == self.Status.PUBLISHED and self.published_until:
            now = timezone.now()
            if now > self.published_until:
                return True
        return False
