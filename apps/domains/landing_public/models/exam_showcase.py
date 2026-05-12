"""공개 성적 통계 — 학원장이 1버튼으로 게시한 시험별 익명 석차 (Phase #13).

본질: 매치업과 함께 학원 홍보 본질 = 객관적 성적 통계의 정기 노출.
선생앱 성적탭 1버튼 → 시험 1개 × 전체 수강생 익명 석차+점수 → 랜딩 자동 노출.

학생 개인정보 보호:
  - 학생 이름은 anonymization_mode 정책으로 마스킹
    - "initial": "박○○", "김○○○" (성 + 동그라미)
    - "phone_last4": "박학생 (1234)" (이름 + 전번 뒷자리)
    - "pseudonym": "학원장이 지정한 익명 ID"
  - 학생 phone / email / 주소 절대 노출 X
  - 학원장이 publish 시점에 snapshot 저장 — 학생 정보 수정해도 게시본 영향 X

publish 후 immutable: 시험 점수 수정/학생 탈퇴해도 게시판 본 데이터 유지.
종료 날짜 도래 시 외부 카드 메타만 노출 (상세 list 차단). 학원장은 영구 열람.
"""
from django.db import models

from apps.core.models.base import TimestampModel


class PublicExamShowcase(TimestampModel):
    """공개 시험 성적 showcase."""

    class AnonymizationMode(models.TextChoices):
        INITIAL = "initial", "성+○○ 마스킹"
        PHONE_LAST4 = "phone_last4", "이름+전번 뒷자리"
        PSEUDONYM = "pseudonym", "익명 ID"

    class Status(models.TextChoices):
        DRAFT = "draft", "초안"
        PUBLISHED = "published", "공개"
        EXPIRED = "expired", "기간 만료 (요약만 노출)"
        HIDDEN = "hidden", "비공개"

    tenant = models.ForeignKey(
        "core.Tenant",
        on_delete=models.CASCADE,
        related_name="public_exam_showcases",
        db_index=True,
    )
    # 원본 시험 FK (선택). exam 삭제되어도 snapshot 데이터로 유지.
    exam_id_ref = models.PositiveIntegerField(
        null=True, blank=True, db_index=True,
        help_text="원본 Exam.id (참조용, FK 아님 — exam 삭제 후에도 snapshot 유지)",
    )

    # 학원장 입력
    title = models.CharField(max_length=200, help_text="예: 2025 중간 통합과학 — 고1 결과")
    description = models.TextField(blank=True, default="", help_text="학원장 코멘트 (선택)")

    # 노출 정책
    anonymization_mode = models.CharField(
        max_length=20,
        choices=AnonymizationMode.choices,
        default=AnonymizationMode.INITIAL,
    )
    status = models.CharField(
        max_length=12, choices=Status.choices, default=Status.DRAFT, db_index=True,
    )
    published_at = models.DateTimeField(null=True, blank=True)
    published_until = models.DateField(
        null=True, blank=True,
        help_text="외부 상세 노출 종료 날짜. past 시 카드 요약만 노출. null=영구 노출.",
    )

    # snapshot — immutable 데이터 (publish 시 backfill, 이후 변경 X)
    # rows: [{display_name, score, max_score, rank, total, ...optional grade/subject 메타}]
    rows = models.JSONField(default=list, blank=True)
    # summary: {avg, max, min, count, pass_count, fail_count, ...}
    summary = models.JSONField(default=dict, blank=True)
    snapshot_at = models.DateTimeField(null=True, blank=True, help_text="snapshot 생성 시각")

    # 작성자 (학원장 owner/admin/teacher)
    created_by = models.ForeignKey(
        "core.User",
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="created_public_exam_showcases",
    )

    # 외부 view count (학부모가 본 횟수 — 학원장이 효과 측정용)
    view_count = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "landing_public_exam_showcase"
        ordering = ["-published_at", "-created_at"]
        indexes = [
            models.Index(fields=["tenant", "status", "-published_at"]),
            models.Index(fields=["tenant", "exam_id_ref"]),
        ]

    def __str__(self):
        return f"PublicExamShowcase(tenant={self.tenant_id}, id={self.pk}, title={self.title[:40]})"
