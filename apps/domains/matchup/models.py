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


class MatchupHitReport(TimestampModel):
    """시험지 1 doc 단위로 사람이 큐레이션한 적중 보고서.

    실장이 매치업이 자동으로 찾아준 후보 중 적합한 것을 골라 코멘트·해설을 붙여
    선생/학원장에게 제출하는 보고서. 학원 운영의 핵심 비즈니스 산출물.

    자동 PDF 보고서(hit-report.pdf)와는 분리:
      - hit-report.pdf  = 시스템이 top1 매칭으로 자동 생성 (마케팅/네이버 카페용)
      - hit-report      = 사람이 후보를 골라 큐레이션 (선생 보고서/학원 내부용)
    """
    objects = TenantQuerySet.as_manager()

    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="matchup_hit_reports",
        db_index=True,
    )
    document = models.OneToOneField(
        MatchupDocument,
        on_delete=models.CASCADE,
        related_name="hit_report",
    )
    title = models.CharField(max_length=255, blank=True, default="")
    summary = models.TextField(blank=True, default="")  # 보고서 상단 메모/설명

    STATUS_CHOICES = [
        ("draft", "작성중"),
        ("submitted", "제출됨"),
    ]
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default="draft", db_index=True,
    )
    submitted_at = models.DateTimeField(null=True, blank=True)
    submitted_by_id = models.IntegerField(null=True, blank=True)
    submitted_by_name = models.CharField(max_length=100, blank=True, default="")

    class Meta:
        app_label = "matchup"
        ordering = ["-updated_at"]

    def __str__(self):
        return f"HitReport doc#{self.document_id} ({self.status})"


class MatchupHitReportEntry(TimestampModel):
    """문항 단위 큐레이션 엔트리. exam doc의 problem 1개당 1개.

    selected_problem_ids: 사용자가 선택한 학원 자료 problem id 목록 (multi).
    comment: 사용자가 직접 작성한 코멘트/해설 (학원이 어떻게 가르쳤는지 등).
    """
    objects = TenantQuerySet.as_manager()

    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="matchup_hit_report_entries",
        db_index=True,
    )
    report = models.ForeignKey(
        MatchupHitReport,
        on_delete=models.CASCADE,
        related_name="entries",
    )
    exam_problem = models.ForeignKey(
        MatchupProblem,
        on_delete=models.CASCADE,
        related_name="hit_report_entries",
    )
    selected_problem_ids = models.JSONField(default=list, blank=True)
    comment = models.TextField(blank=True, default="")
    order = models.PositiveIntegerField(default=0)

    class Meta:
        app_label = "matchup"
        ordering = ["order", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["report", "exam_problem"],
                name="unique_hit_report_exam_problem",
            ),
        ]

    def __str__(self):
        return f"Entry report#{self.report_id} exam_q={self.exam_problem_id}"
