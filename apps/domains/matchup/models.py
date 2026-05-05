# PATH: apps/domains/matchup/models.py
# AI 매치업 — 문제 문서 + 추출 문제 모델

from django.db import models
from apps.core.models.base import TimestampModel
from apps.core.models import Tenant
from apps.core.db import TenantQuerySet


class MatchupDocument(TimestampModel):
    """업로드된 문제 문서 (PDF/이미지).

    author = 자료를 업로드한 강사. 매치업 보고서 = 강사 1인 포트폴리오 철학에서
    저작권 격리의 baseline. NULL=legacy/공용 풀 — find_similar에서 모든 강사가
    후보로 사용 가능 (구버전 데이터 보호).
    """
    objects = TenantQuerySet.as_manager()

    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="matchup_documents",
        db_index=True,
    )
    author = models.ForeignKey(
        "core.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="matchup_documents_authored",
        db_index=True,
        help_text="자료 업로더(소유 강사). NULL=legacy 공용 풀.",
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
    """문서에서 추출된 개별 문제.

    매치업 보고서 우 pane(강사 수업자료)의 단위. document.author로 강사 격리.
    """
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
    """프리랜서 강사 1인이 작성하는 매치업 적중 보고서.

    역할: 동일 보고서가 (1) 강사의 수업 히스토리, (2) 소속 학원에 정기 제출하는 KPI,
    (3) 신규 학원/학부모/카페 대상 신뢰자료+홍보물 의 3가지로 동시에 사용된다.

    구조: 카테고리당 시험지 1장 + 강사 1명 = 보고서 1건. 같은 시험지에 여러 강사가
    각자 보고서를 만들 수 있고, 각 보고서는 자기 author의 자료만 큐레이션 후보로 본다.

    좌 pane = 학생이 제출한 학교 시험지. 우 pane = 그 강사 본인이 수업에 쓴 자료.
    """
    objects = TenantQuerySet.as_manager()

    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="matchup_hit_reports",
        db_index=True,
    )
    # 시험지 doc — 카테고리당 1장 가정. 강사별 별개 보고서 가능 → ForeignKey + UniqueConstraint(document, author).
    document = models.ForeignKey(
        MatchupDocument,
        on_delete=models.CASCADE,
        related_name="hit_reports",
    )
    # 보고서 작성 강사 (소유자). NULL=legacy.
    author = models.ForeignKey(
        "core.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="matchup_hit_reports_authored",
        db_index=True,
        help_text="보고서 작성 강사. submitted_by_id는 deprecated (호환 보존).",
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
    # deprecated: author FK로 대체. 호환을 위해 보존하되 신규 코드는 author 사용.
    submitted_by_id = models.IntegerField(null=True, blank=True)
    submitted_by_name = models.CharField(max_length=100, blank=True, default="")

    class Meta:
        app_label = "matchup"
        ordering = ["-updated_at"]
        constraints = [
            # 같은 강사가 같은 시험지에 보고서 2건 작성 차단.
            # author=NULL은 PostgreSQL NULL semantics로 자동 면제 — legacy 보고서 보호.
            models.UniqueConstraint(
                fields=["document", "author"],
                name="unique_hit_report_doc_author",
            ),
        ]

    def __str__(self):
        return f"HitReport doc#{self.document_id} author#{self.author_id} ({self.status})"


class MatchupHitReportEntry(TimestampModel):
    """문항 단위 큐레이션 엔트리. 시험지 problem 1개당 1개.

    selected_problem_ids: 강사가 선택한 본인 수업자료 problem id 목록 (multi).
    comment: 강사 본인이 작성한 지도 코멘트/해설 (수업 노트).
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
    # 강사가 매칭 못시킨/큐레이션 의도가 없는 시험지 문항을 PDF에서 빼고 싶을 때 ON.
    # PDF 렌더 + 적중률(분모/분자) 모두 skip. UI 좌측 Q 리스트 토글 (2026-05-05).
    excluded = models.BooleanField(default=False)

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
