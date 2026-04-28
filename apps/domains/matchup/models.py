# PATH: apps/domains/matchup/models.py
# AI 매치업 — 문제 문서 + 추출 문제 모델

from django.db import models
from apps.core.models.base import TimestampModel
from apps.core.models import Tenant
from apps.core.db import TenantQuerySet


class MatchupDocument(TimestampModel):
    """업로드된 문제 문서 (PDF/이미지)."""
    objects = TenantQuerySet.as_manager()

    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="matchup_documents",
        db_index=True,
    )
    # 저장소(InventoryFile) 위의 분석 레이어 (storage-as-canonical).
    inventory_file = models.OneToOneField(
        "inventory.InventoryFile",
        on_delete=models.CASCADE,
        related_name="matchup_document",
    )
    title = models.CharField(max_length=255)
    # 섹션/카테고리 (예: 중대부고, 숙명여고). 같은 카테고리끼리만 추천에 사용.
    category = models.CharField(max_length=100, blank=True, default="", db_index=True)
    subject = models.CharField(max_length=100, blank=True, default="")
    grade_level = models.CharField(max_length=50, blank=True, default="")
    r2_key = models.CharField(max_length=512, unique=True, db_index=True)
    original_name = models.CharField(max_length=255)
    size_bytes = models.BigIntegerField(default=0)
    content_type = models.CharField(max_length=128, default="application/pdf")

    STATUS_CHOICES = [
        ("pending", "대기"),
        ("processing", "처리중"),
        ("done", "완료"),
        ("failed", "실패"),
    ]
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="pending",
        db_index=True,
    )
    ai_job_id = models.CharField(max_length=36, blank=True, default="")
    problem_count = models.PositiveIntegerField(default=0)
    error_message = models.TextField(blank=True, default="")
    # 운영 관측용 메타 (segmentation_method, has_text_pages 등)
    meta = models.JSONField(default=dict, blank=True)

    class Meta:
        app_label = "matchup"
        ordering = ["-created_at"]

    def __str__(self):
        return f"[{self.tenant_id}] {self.title} ({self.status})"


class MatchupProblem(TimestampModel):
    """문서에서 추출된 개별 문제."""
    objects = TenantQuerySet.as_manager()

    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="matchup_problems",
        db_index=True,
    )
    document = models.ForeignKey(
        MatchupDocument,
        on_delete=models.CASCADE,
        related_name="problems",
        null=True,
        blank=True,
    )
    number = models.PositiveIntegerField()
    text = models.TextField(blank=True, default="")
    image_key = models.CharField(max_length=512, blank=True, default="")
    embedding = models.JSONField(null=True, blank=True)
    # CLIP image embedding (cropped problem 이미지) — 텍스트 임베딩이 약한 카메라 사진/
    # 스캔본의 매치업 정확도 보강. find_similar_problems가 ensemble 가중평균 적용.
    image_embedding = models.JSONField(null=True, blank=True)
    meta = models.JSONField(default=dict, blank=True)

    # 출처 추적 — 시험 문제 인덱싱 시 사용
    SOURCE_CHOICES = [
        ("matchup", "매치업 업로드"),
        ("exam", "시험 문제"),
    ]
    source_type = models.CharField(
        max_length=20, choices=SOURCE_CHOICES, default="matchup", db_index=True,
    )
    source_exam_id = models.IntegerField(null=True, blank=True, db_index=True)
    source_question_number = models.IntegerField(null=True, blank=True)
    # 역추적용 비정규화 (JOIN 없이 바로 표시)
    source_lecture_title = models.CharField(max_length=255, blank=True, default="")
    source_session_title = models.CharField(max_length=255, blank=True, default="")
    source_exam_title = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        app_label = "matchup"
        ordering = ["number"]
        constraints = [
            # 매치업 업로드 문서 내 중복 방지
            models.UniqueConstraint(
                fields=["document", "number"],
                condition=models.Q(document__isnull=False),
                name="unique_matchup_doc_number",
            ),
            # 시험 인덱싱 중복 방지
            models.UniqueConstraint(
                fields=["tenant", "source_exam_id", "source_question_number"],
                condition=models.Q(source_type="exam"),
                name="unique_matchup_exam_question",
            ),
        ]

    def __str__(self):
        return f"Doc {self.document_id} Q{self.number}"
