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

    # Audit log — 학원장 작성 데이터 immutable 원칙 (Stage 2, 2026-05-06).
    # 사용자 directive: selected_problem_ids 변경은 모두 history에 추적.
    # 자동 reanalyze / 자동 매핑 / AI callback 직접 수정 시 source 명시 필수.
    # PITR 없이도 특정 시점 selected_problem_ids 복원 가능 (selection_history 사용).
    #
    # schema:
    # [{
    #   "timestamp": "2026-05-06T12:30:00Z",
    #   "previous_selected_ids": [...],
    #   "new_selected_ids": [...],
    #   "changed_by_id": int,
    #   "change_source": "user_ui" | "admin_pitr_restore" | "admin_repair" | ...,
    #   "reason": str,
    # }, ...]
    selection_history = models.JSONField(default=list, blank=True)
    last_modified_by = models.ForeignKey(
        "core.User", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="modified_matchup_entries",
    )

    class Meta:
        app_label = "matchup"
        ordering = ["order", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["report", "exam_problem"],
                name="unique_hit_report_exam_problem",
            ),
        ]

    def append_selection_history(
        self,
        *,
        new_selected_ids: list,
        by_user_id: int | None = None,
        source: str = "user_ui",
        reason: str = "",
    ) -> None:
        """selected_problem_ids 변경 직전 호출 — audit log append.

        Args:
            new_selected_ids: 새 selected_problem_ids 값
            by_user_id: 변경자 user.id (사용자 UI / admin)
            source: "user_ui" / "admin_pitr_restore" / "admin_repair" / "migration"
            reason: 변경 사유 (PITR 복원, 수동 정정 등)

        주의: 이 함수는 self.selected_problem_ids 자체를 변경하지 않는다.
        호출자가 history append 후 명시적으로 selected_problem_ids 갱신해야 함.
        """
        from django.utils import timezone
        prev_ids = list(self.selected_problem_ids or [])
        new_ids = list(new_selected_ids or [])
        if prev_ids == new_ids:
            return  # no-op
        history = list(self.selection_history or [])
        history.append({
            "timestamp": timezone.now().isoformat(),
            "previous_selected_ids": prev_ids,
            "new_selected_ids": new_ids,
            "changed_by_id": by_user_id,
            "change_source": source,
            "reason": reason,
        })
        self.selection_history = history
        if by_user_id:
            self.last_modified_by_id = by_user_id

    def __str__(self):
        return f"Entry report#{self.report_id} exam_q={self.exam_problem_id}"


class ProblemSegmentationProposal(TimestampModel):
    """AI 문항 분리 결과 — 운영 문항(MatchupProblem)과 구조 분리 (Stage 3, 2026-05-06).

    AI 결과(VLM/YOLO/OCR)는 ConfirmedProblem이 아니라 proposal이다.
    승인 후에만 MatchupProblem으로 승격.

    원칙:
    - proposal은 추천 풀에 들어가지 않는다 (find_similar 후보 X).
    - proposal은 selected_problem_ids에 참조되지 않는다.
    - 승인 전 indexable=False (실효는 status 필드).
    - 학원장 manual=True cut 영역과 겹치는 proposal은 자동 status='rejected'
      (validation_errors에 'manual_overlap' reason 기록).

    승격 path:
        ProblemSegmentationProposal(status='approved')
            → MatchupProblem 생성 (transaction.atomic, audit log)
            → ProblemSegmentationProposal.status='approved' 유지 (audit 보존)
    """

    objects = TenantQuerySet.as_manager()

    STATUS_CHOICES = [
        ("pending", "검수 대기"),
        ("needs_review", "검수 필수"),
        ("rejected", "거절"),
        ("approved", "승인 완료"),
        ("auto_passed", "자동 통과 (validator)"),
    ]

    ENGINE_CHOICES = [
        ("yolo", "YOLO segmentation"),
        ("vlm", "VLM (Gemini)"),
        ("ocr", "OCR + layout heuristic"),
        ("native_pdf", "Native PDF parser"),
        ("manual_assist", "사용자 수동 자르기 보조"),
    ]

    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="matchup_segmentation_proposals",
        db_index=True,
    )
    document = models.ForeignKey(
        MatchupDocument,
        on_delete=models.CASCADE,
        related_name="segmentation_proposals",
        db_index=True,
    )
    # AI 분석 batch 식별자 — 같은 batch의 proposal 묶음 그룹화 / rerun 비교용.
    # job_id 또는 application 정의 키 (예: "yolo-v11-2026-05-06-doc321") 자유 형식.
    analysis_version_key = models.CharField(max_length=128, blank=True, default="", db_index=True)

    page_number = models.IntegerField(default=0, db_index=True)
    # bbox JSON: {"x": float, "y": float, "w": float, "h": float, "norm": bool}
    # norm=True 면 0~1 normalized, False 면 px. callback이 채울 때 명시.
    bbox = models.JSONField(default=dict, blank=True)
    detected_problem_number = models.IntegerField(default=0)

    engine = models.CharField(max_length=32, choices=ENGINE_CHOICES, db_index=True)
    model_version = models.CharField(max_length=64, blank=True, default="")
    confidence = models.FloatField(default=0.0)

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="pending",
        db_index=True,
    )

    # R2 객체 키 — 잘린 problem 이미지가 있다면 보관 (preview용). 승인 시 MatchupProblem으로 이전.
    image_key = models.CharField(max_length=512, blank=True, default="")

    # AI 원본 응답 — 디버깅/audit. 임의 JSON.
    raw_response = models.JSONField(default=dict, blank=True)

    # validator 검출 오류 / 거절 사유 — schema:
    # [{"code": "manual_overlap", "detail": "...", "bbox_iou": 0.42}, ...]
    validation_errors = models.JSONField(default=list, blank=True)

    # 승인/거절 시 누가/언제 — audit. status='pending'이면 둘 다 NULL.
    reviewed_by = models.ForeignKey(
        "core.User",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="reviewed_segmentation_proposals",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)

    # 승인된 proposal이 어떤 MatchupProblem으로 승격됐는지 trace.
    promoted_problem = models.ForeignKey(
        "MatchupProblem",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="source_proposals",
    )

    class Meta:
        app_label = "matchup"
        ordering = ["document_id", "page_number", "detected_problem_number"]
        indexes = [
            models.Index(fields=["tenant", "document", "status"]),
            models.Index(fields=["tenant", "status", "engine"]),
            models.Index(fields=["analysis_version_key"]),
        ]

    def __str__(self):
        return (
            f"Proposal doc#{self.document_id} p{self.page_number} "
            f"q{self.detected_problem_number} [{self.status}/{self.engine}]"
        )


class TenantSegmentationProfile(TimestampModel):
    """tenant 별 segmentation 정책 (Stage 5.4.6, 2026-05-06).

    학원/강사마다 자료 양식이 다르므로 전역 모델이 아닌 tenant 단위로 학습된 profile
    저장. cross-tenant 공유 영구 금지 — 다른 학원 manual cut 으로 우리 tenant
    threshold 학습 안 함.

    원칙:
    - is_active=False default — feature flag, 관리자 명시 활성화 후 사용
    - profile 자동 업데이트 worker 는 Stage 5.5+ 영역 — 이번 commit 은 schema 만
    - GlobalPolicy fallback (fallback_to_global=True) — 학습 데이터 부족 시 안전 기본값
    """

    objects = TenantQuerySet.as_manager()

    tenant = models.OneToOneField(
        Tenant,
        on_delete=models.CASCADE,
        related_name="segmentation_profile",
    )

    # gradual rollout — feature flag
    is_active = models.BooleanField(default=False, db_index=True)
    fallback_to_global = models.BooleanField(default=True)
    profile_version = models.PositiveIntegerField(default=1)

    # paper_type 별 임계값 (operate prototype 위에 학습)
    # 예: {"exam": {"expected_max": 30, "auto_approve_threshold": 0.85}, ...}
    paper_type_thresholds = models.JSONField(default=dict, blank=True)
    # 예: {"exam": 30, "review_homework": 80, "advanced_material": 200, ...}
    paper_type_expected_max = models.JSONField(default=dict, blank=True)
    # 예: {"exam": 0.85, "workbook_main": 0.9, ...}
    auto_approve_thresholds = models.JSONField(default=dict, blank=True)

    # tenant 자료 layout 패턴 (manual cut 통계로 학습)
    # 예: [{"name": "single_column", "ratio": 0.62}, {"name": "two_column", "ratio": 0.36}]
    common_layout_clusters = models.JSONField(default=list, blank=True)
    # 예: {"x0_clusters": [0.05, 0.50], "width_p50": 0.42, ...}
    bbox_stats = models.JSONField(default=dict, blank=True)
    # 예: {"1": 0.62, "2": 0.36, "4": 0.02}
    column_count_distribution = models.JSONField(default=dict, blank=True)

    # 운영 통계 — feedback loop 입력
    scanned_pdf_ratio = models.FloatField(null=True, blank=True)
    manual_correction_rate = models.FloatField(null=True, blank=True)
    vlm_needed_ratio = models.FloatField(null=True, blank=True)

    # profile 신뢰도 (학습 데이터 양 + 자동 승인률 등 종합)
    confidence_score = models.FloatField(default=0.0)
    samples_used = models.IntegerField(default=0)
    last_profiled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        app_label = "matchup"
        indexes = [
            models.Index(fields=["tenant", "is_active"]),
        ]

    def __str__(self):
        return (
            f"TenantProfile(tenant={self.tenant_id}, v{self.profile_version}, "
            f"active={self.is_active}, conf={self.confidence_score:.2f})"
        )


class LayoutFingerprint(TimestampModel):
    """document 별 레이아웃 지문 (Stage 5.4.6, 2026-05-06).

    같은 tenant 안에서 비슷한 layout 의 doc 을 그루핑 — manual correction 패턴 reuse.
    cross-tenant 매칭 영구 금지.

    원칙:
    - tenant FK 필수 — 다른 학원 자료와 매칭 안 함
    - similarity_cluster_id 는 tenant 안에서만 의미 있음
    - 이번 commit 은 schema 만 — 자동 fingerprint 생성 worker 는 Stage 5.5+ 영역
    """

    objects = TenantQuerySet.as_manager()

    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="layout_fingerprints",
        db_index=True,
    )
    document = models.ForeignKey(
        MatchupDocument,
        on_delete=models.CASCADE,
        related_name="layout_fingerprints",
    )

    paper_type = models.CharField(max_length=32, blank=True, default="", db_index=True)
    fingerprint_version = models.PositiveIntegerField(default=1)

    # 페이지 메타
    page_count = models.IntegerField(default=0)
    page_size = models.JSONField(default=dict, blank=True)  # {"width": 595, "height": 842}

    # 레이아웃 시그널
    text_density = models.FloatField(default=0.0)
    image_density = models.FloatField(default=0.0)
    column_count = models.IntegerField(default=1)
    anchor_density = models.FloatField(default=0.0)

    # bbox 분포 시그널 (예: x0_clusters=[0.05, 0.50])
    x0_clusters = models.JSONField(default=list, blank=True)
    y_gap_distribution = models.JSONField(default=dict, blank=True)
    font_size_distribution = models.JSONField(default=dict, blank=True)

    # filename / category 패턴
    filename_patterns = models.JSONField(default=list, blank=True)

    # 같은 tenant 안 비슷한 layout doc 그루핑 — cross-tenant 매칭 X
    similarity_cluster_id = models.CharField(
        max_length=64, blank=True, default="", db_index=True,
    )

    class Meta:
        app_label = "matchup"
        indexes = [
            models.Index(fields=["tenant", "paper_type"]),
            models.Index(fields=["tenant", "similarity_cluster_id"]),
            models.Index(fields=["tenant", "document", "fingerprint_version"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "document", "fingerprint_version"],
                name="uniq_layout_fingerprint_per_doc_version",
            ),
        ]

    def __str__(self):
        return (
            f"Fingerprint doc#{self.document_id} v{self.fingerprint_version} "
            f"pt={self.paper_type}"
        )


class ManualCorrectionDelta(TimestampModel):
    """학원장 manual correction audit log (Stage 5.4.6, 2026-05-06).

    proposal 검수 / 별 토글 / bbox 조정 / split / merge 등 사용자 개입을
    audit. 이 델타가 누적되면 TenantSegmentationProfile 학습 신호로 사용.

    원칙:
    - read-only audit — 본 모델은 selected_problem_ids / hit_report 변경 X
    - tenant FK 필수
    - manual cut 자체는 immutable — 이 델타는 검수 기록만
    - Stage 5.5+ worker 가 이 델타로 tenant profile 업데이트
    """

    CORRECTION_TYPE_CHOICES = [
        ("approve", "approve"),
        ("reject", "reject"),
        ("bbox_adjust", "bbox_adjust"),
        ("split", "split"),
        ("merge", "merge"),
        ("manual_create", "manual_create"),
        ("number_adjust", "number_adjust"),
        ("text_adjust", "text_adjust"),
    ]

    SOURCE_CHOICES = [
        ("user_ui", "user_ui"),
        ("admin_review", "admin_review"),
    ]

    objects = TenantQuerySet.as_manager()

    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="manual_correction_deltas",
        db_index=True,
    )
    # nullable — proposal 외 다른 path 의 manual correction (bbox 직접 조정 등) 도 기록 가능
    proposal = models.ForeignKey(
        ProblemSegmentationProposal,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="correction_deltas",
    )
    problem = models.ForeignKey(
        MatchupProblem,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="correction_deltas",
    )
    document = models.ForeignKey(
        MatchupDocument,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="correction_deltas",
    )

    correction_type = models.CharField(
        max_length=32,
        choices=CORRECTION_TYPE_CHOICES,
        db_index=True,
    )
    source = models.CharField(
        max_length=16,
        choices=SOURCE_CHOICES,
        default="user_ui",
    )

    # bbox audit (proposal/problem 의 원 bbox vs 사용자 수정 bbox)
    original_bbox = models.JSONField(null=True, blank=True)
    corrected_bbox = models.JSONField(null=True, blank=True)
    iou_with_ai = models.FloatField(null=True, blank=True)

    # 부가 메타
    paper_type_at_action = models.CharField(max_length=32, blank=True, default="")
    engine_at_action = models.CharField(max_length=32, blank=True, default="")
    notes = models.TextField(blank=True, default="")

    created_by = models.ForeignKey(
        "core.User",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="matchup_correction_deltas",
    )

    class Meta:
        app_label = "matchup"
        indexes = [
            models.Index(fields=["tenant", "correction_type", "created_at"]),
            models.Index(fields=["tenant", "paper_type_at_action"]),
            models.Index(fields=["tenant", "document"]),
        ]

    def __str__(self):
        return (
            f"CorrectionDelta tenant={self.tenant_id} type={self.correction_type} "
            f"proposal={self.proposal_id} problem={self.problem_id}"
        )


class MatchupPageState(TimestampModel):
    """페이지별 처리 상태 — 학원장이 자동/수동/제외를 페이지 단위로 결정.

    matchup 분리 기능 base (2026-05-09 사용자 directive):
      합격선 = 'AI 가 완벽히 자동 cut' 이 아니라 '최종 Problem Image Set
      을 학원장이 최소 노동으로 확정' 이다. 그 1단계가 page-level 분기.

    state 종류:
      auto    — 자동분리 실행 (현행 default). YOLO/VLM/OCR 후보 → 검수 → 최종.
      skip    — 매치업 인덱싱 X (표지/목차/해설/답안지 등 비문항 페이지).
                기존 doc.meta.excluded_pages 와 호환 — 동기화 helper 가 양방향 변환.
      manual  — 자동분리 X. 학원장 manual cut 만 final 로 사용 (자동 noise 차단).

    backward compat:
      doc.meta.excluded_pages 가 SSOT 역할을 그대로 유지 (worker 기존 path).
      PageState.state='skip' 인 page_index 는 excluded_pages 에 동기화.
      신규 코드는 PageState 우선 — sync helper 가 단방향(state→meta) 갱신.

    blast radius 격리:
      신규 모델만 추가. 기존 MatchupProblem / MatchupDocument / callback 변경 0.
      worker 가 PageState 를 직접 읽을지는 후속 Phase D (feature flag).
    """
    objects = TenantQuerySet.as_manager()

    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="matchup_page_states",
        db_index=True,
    )
    document = models.ForeignKey(
        MatchupDocument,
        on_delete=models.CASCADE,
        related_name="page_states",
    )
    page_index = models.PositiveIntegerField()

    STATE_CHOICES = [
        ("auto", "자동 분리"),
        ("skip", "건너뛰기"),
        ("manual", "직접 자르기만"),
    ]
    state = models.CharField(
        max_length=10,
        choices=STATE_CHOICES,
        default="auto",
        db_index=True,
    )

    # 마지막 변경 사용자 audit (학원장 vs 시스템 자동 추천 구분).
    # 자동 추천 (paper_type_summary 기반 cover/explanation skip 자동) = NULL.
    updated_by = models.ForeignKey(
        "core.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        help_text="마지막 변경 사용자. NULL=시스템 자동 추천.",
    )

    # 시스템 자동 추천일 때 근거. 학원장이 수동 변경 시 클리어.
    # 가능 값: paper_type_cover / paper_type_explanation / paper_type_answer_key /
    #         paper_type_index / vlm_classify_<role> / 기타.
    auto_reason = models.CharField(
        max_length=64, blank=True, default="",
        help_text="자동 state 추천 근거. 학원장 수동 변경 시 클리어.",
    )

    class Meta:
        app_label = "matchup"
        ordering = ["document_id", "page_index"]
        constraints = [
            models.UniqueConstraint(
                fields=["document", "page_index"],
                name="unique_matchup_page_state",
            ),
        ]
        indexes = [
            models.Index(fields=["tenant", "document"]),
            models.Index(fields=["tenant", "state"]),
        ]

    def __str__(self):
        return f"PageState doc={self.document_id} p{self.page_index} {self.state}"


class AutoSegmentationSnapshot(TimestampModel):
    """자동분리 결과 스냅샷 — fine-tune loop 가동 base (V11 BOTTLENECK §7.1).

    문제:
      callback `_handle_matchup_ai_result` 가 자동 cut 결과를 MatchupProblem 으로
      bulk_create 만 하고 끝 — 학원장이 manual cut 으로 덮어쓰면 자동 cut 이 어떤
      box 였는지 audit 없음. ManualCorrectionDelta 의 `original_bbox` /
      `iou_with_ai` / `proposal_fk` 모두 0% 보존 (실측, 2026-05-08).
      → "manual 이 어떻게 자동을 교정했는가" 학습 신호 끊김.

    해결:
      callback 안에서 problems_data → AutoSegmentationSnapshot bulk_create.
      manual_crop hook 안에서 같은 (document, page_index) snapshot 찾아 IoU 비교
      → ManualCorrectionDelta.original_bbox / iou_with_ai 자동 채움.

    fine-tune loop:
      Snapshot (자동 cut) ↔ ManualCorrectionDelta (학원장 교정) → V13/V14 학습 시
      'manual diff' 직접 신호로 사용. paradigm 한계 돌파 path.

    blast radius:
      신규 모델만 추가. 기존 callback / MatchupProblem / ManualCorrectionDelta
      schema 변경 0. callback 안 bulk_create instrument 만 추가 (default on,
      try/except fail-soft).
    """
    objects = TenantQuerySet.as_manager()

    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="auto_segmentation_snapshots",
        db_index=True,
    )
    document = models.ForeignKey(
        MatchupDocument,
        on_delete=models.CASCADE,
        related_name="auto_segmentation_snapshots",
    )

    # AI dispatch job id — 같은 reanalyze 의 snapshot 묶음 그룹화 / rerun 비교용.
    job_id = models.CharField(max_length=64, blank=True, default="", db_index=True)

    # bbox 기준 페이지. ManualCorrectionDelta lookup key.
    page_index = models.PositiveIntegerField(db_index=True)

    # 자동 cut 의 원 number (V11 detect 결과). manual 시점 매칭에 보조.
    detected_problem_number = models.IntegerField(default=0)

    # bbox JSON: {x, y, w, h, page} 또는 {x_norm, y_norm, ...}.
    # callback 시점 problems_data[].meta.bbox 와 동일 schema 보존 (변환 X).
    bbox = models.JSONField(default=dict, blank=True)

    # 분리 엔진 (yolo_v11 / yolo_v12 / vlm / hybrid / native_pdf 등).
    # ProblemSegmentationProposal.ENGINE_CHOICES 와 align.
    ENGINE_CHOICES = [
        ("yolo", "YOLO segmentation"),
        ("yolo_v11", "YOLO V11"),
        ("yolo_v12", "YOLO V12"),
        ("yolo_v13", "YOLO V13"),
        ("vlm", "VLM (Gemini)"),
        ("ocr", "OCR + layout heuristic"),
        ("native_pdf", "Native PDF parser"),
        ("hybrid", "Hybrid (YOLO + VLM verifier)"),
        ("manual_assist", "사용자 수동 자르기 보조"),
        ("unknown", "엔진 미상"),
    ]
    engine = models.CharField(
        max_length=32,
        choices=ENGINE_CHOICES,
        default="unknown",
        db_index=True,
    )
    engine_version = models.CharField(max_length=64, blank=True, default="")

    confidence = models.FloatField(default=0.0)

    # multi-class 학습 대비 (현재 single-class 'problem' 만, 향후 확장).
    class_id = models.IntegerField(default=0)
    class_name = models.CharField(max_length=32, default="problem")

    # 후처리 stage 표식 (학습 신호용):
    #   raw_yolo / hybrid_filtered / auto_merged / area_filtered / over_crop_padded
    # 학습 시 어떤 후처리 단계에서 keep/drop 됐는지 audit.
    post_process_stage = models.CharField(max_length=32, blank=True, default="")

    # MatchupProblem 으로 승격됐는지 추적 (callback 의 bulk_create 결과 매칭).
    promoted_problem = models.ForeignKey(
        "MatchupProblem",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="source_snapshots",
    )

    class Meta:
        app_label = "matchup"
        ordering = ["document_id", "page_index", "detected_problem_number"]
        indexes = [
            models.Index(fields=["tenant", "document", "page_index"]),
            models.Index(fields=["tenant", "engine", "engine_version"]),
            models.Index(fields=["job_id"]),
        ]

    def __str__(self):
        return (
            f"AutoSnapshot doc={self.document_id} p{self.page_index} "
            f"#{self.detected_problem_number} {self.engine}@{self.engine_version}"
        )
