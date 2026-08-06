from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models

from apps.api.common.models import TimestampModel
from apps.core.db import TenantQuerySet
from apps.core.models import Tenant


class ProblemStudioFontAsset(TimestampModel):
    class Status(models.TextChoices):
        READY = "ready", "사용 가능"
        DISABLED = "disabled", "사용 중지"

    class LicenseBasis(models.TextChoices):
        PURCHASED = "purchased", "직접 구매"
        FREE = "free", "무료 배포"
        ACADEMY = "academy", "학원 보유"
        OTHER = "other", "기타"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="problem_studio_font_assets",
        db_index=True,
    )
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="problem_studio_font_assets",
    )
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.READY, db_index=True)

    display_name = models.CharField(max_length=160)
    family_name = models.CharField(max_length=160)
    subfamily_name = models.CharField(max_length=160, blank=True, default="")
    full_name = models.CharField(max_length=200, blank=True, default="")
    postscript_name = models.CharField(max_length=200, blank=True, default="")
    font_revision = models.CharField(max_length=40, blank=True, default="")

    original_name = models.CharField(max_length=255)
    r2_key = models.CharField(max_length=512, unique=True)
    size_bytes = models.PositiveBigIntegerField()
    content_type = models.CharField(max_length=100)
    sha256 = models.CharField(max_length=64, db_index=True)
    file_format = models.CharField(max_length=8)

    glyph_count = models.PositiveIntegerField(default=0)
    supports_hangul = models.BooleanField(default=False)
    supports_latin = models.BooleanField(default=False)
    fs_type = models.PositiveIntegerField(default=0)
    embedding_permission = models.CharField(max_length=32, default="installable")
    no_subsetting = models.BooleanField(default=False)

    license_basis = models.CharField(max_length=20, choices=LicenseBasis.choices)
    license_name = models.CharField(max_length=160, blank=True, default="")
    license_url = models.URLField(max_length=500, blank=True, default="")
    license_note = models.TextField(blank=True, default="")
    rights_confirmed_at = models.DateTimeField()
    redistribution_allowed = models.BooleanField(default=False)

    objects = TenantQuerySet.as_manager()

    class Meta:
        db_table = "problem_studio_font_asset"
        ordering = ["family_name", "subfamily_name", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "uploaded_by", "sha256"],
                name="uq_problem_studio_font_owner_sha",
            ),
        ]
        indexes = [
            models.Index(
                fields=["tenant", "uploaded_by", "status"],
                name="idx_ps_font_owner_status",
            ),
        ]


class ProblemStudioDocumentStyle(TimestampModel):
    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="problem_studio_document_styles",
        db_index=True,
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="problem_studio_document_styles",
    )
    title_font_key = models.CharField(max_length=40, default="hamchorom-dotum")
    title_font_asset = models.ForeignKey(
        ProblemStudioFontAsset,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    body_font_key = models.CharField(max_length=40, default="hamchorom-batang")
    body_font_asset = models.ForeignKey(
        ProblemStudioFontAsset,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    title_size_pt = models.DecimalField(max_digits=4, decimal_places=1, default=20)
    body_size_pt = models.DecimalField(max_digits=4, decimal_places=1, default=10.5)
    body_width_ratio_percent = models.PositiveSmallIntegerField(
        default=100,
        db_default=100,
    )
    body_letter_spacing_percent = models.SmallIntegerField(
        default=0,
        db_default=0,
    )
    line_spacing_percent = models.PositiveSmallIntegerField(default=155)
    question_spacing_pt = models.DecimalField(max_digits=4, decimal_places=1, default=10)
    match_source_style = models.BooleanField(default=True, db_default=True)

    objects = TenantQuerySet.as_manager()

    class Meta:
        db_table = "problem_studio_document_style"
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "user"],
                name="uq_problem_studio_style_user",
            ),
        ]


class ProblemStudioVoiceProfile(TimestampModel):
    class Status(models.TextChoices):
        ACTIVE = "active", "사용 중"
        ARCHIVED = "archived", "보관"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="problem_studio_voice_profiles",
        db_index=True,
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="problem_studio_voice_profiles",
    )
    name = models.CharField(max_length=80)
    subject = models.CharField(max_length=100, blank=True, default="")
    style_instructions = models.TextField(blank=True, default="")
    is_default = models.BooleanField(default=False)
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.ACTIVE,
        db_index=True,
    )
    version = models.PositiveIntegerField(default=1)

    objects = TenantQuerySet.as_manager()

    class Meta:
        db_table = "problem_studio_voice_profile"
        ordering = ["-is_default", "name", "created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "owner", "name"],
                name="uq_ps_voice_owner_name",
            ),
        ]
        indexes = [
            models.Index(
                fields=["tenant", "owner", "status"],
                name="idx_ps_voice_owner_status",
            ),
        ]


class ProblemStudioVoiceSample(TimestampModel):
    class UsageScope(models.TextChoices):
        STYLE = "style", "문체 학습"
        CONTENT_REFERENCE = "content_reference", "내용 참고"

    class Origin(models.TextChoices):
        TEACHER_AUTHORED = "teacher_authored", "선생님 직접 작성"
        APPROVED_OUTPUT = "approved_output", "검수 승인 결과"
        MATCHUP_COMMENT = "matchup_comment", "매치업 강사 코멘트"
        PUBLISHER_REFERENCE = "publisher_reference", "출판 자료 참고"
        OTHER_REFERENCE = "other_reference", "기타 참고 자료"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="problem_studio_voice_samples",
        db_index=True,
    )
    profile = models.ForeignKey(
        ProblemStudioVoiceProfile,
        on_delete=models.CASCADE,
        related_name="samples",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="problem_studio_voice_samples",
    )
    usage_scope = models.CharField(max_length=24, choices=UsageScope.choices)
    origin = models.CharField(max_length=32, choices=Origin.choices)
    source_label = models.CharField(max_length=160, blank=True, default="")
    problem_text = models.TextField(blank=True, default="")
    answer = models.TextField(blank=True, default="")
    explanation = models.TextField(blank=True, default="")
    fingerprint = models.CharField(max_length=64)
    rights_confirmed_at = models.DateTimeField(null=True, blank=True)
    rights_note = models.CharField(max_length=240, blank=True, default="")
    is_active = models.BooleanField(default=True, db_index=True)
    metadata = models.JSONField(default=dict, blank=True)

    objects = TenantQuerySet.as_manager()

    class Meta:
        db_table = "problem_studio_voice_sample"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["profile", "usage_scope", "fingerprint"],
                name="uq_ps_voice_sample_fingerprint",
            ),
        ]
        indexes = [
            models.Index(
                fields=["tenant", "profile", "usage_scope", "is_active"],
                name="idx_ps_voice_sample_scope",
            ),
        ]


class ProblemStudioGenerationReview(TimestampModel):
    class Outcome(models.TextChoices):
        APPROVED = "approved", "승인"
        EDITED = "edited", "수정 후 승인"
        REJECTED = "rejected", "사용 안 함"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="problem_studio_generation_reviews",
        db_index=True,
    )
    profile = models.ForeignKey(
        ProblemStudioVoiceProfile,
        on_delete=models.SET_NULL,
        null=True,
        related_name="generation_reviews",
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="problem_studio_generation_reviews",
    )
    job_id = models.CharField(max_length=64)
    question_index = models.PositiveSmallIntegerField()
    outcome = models.CharField(max_length=16, choices=Outcome.choices)
    original_payload = models.JSONField(default=dict)
    final_payload = models.JSONField(default=dict)
    feedback_note = models.CharField(max_length=500, blank=True, default="")
    learned_sample = models.OneToOneField(
        ProblemStudioVoiceSample,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="generation_review",
    )

    objects = TenantQuerySet.as_manager()

    class Meta:
        db_table = "problem_studio_generation_review"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "reviewed_by", "job_id", "question_index"],
                name="uq_ps_generation_review_item",
            ),
        ]
        indexes = [
            models.Index(
                fields=["tenant", "reviewed_by", "created_at"],
                name="idx_ps_review_owner_created",
            ),
        ]


class ProblemStudioBetaRun(TimestampModel):
    """Tenant-scoped Beta trial reservation for one full workbook run."""

    class Status(models.TextChoices):
        RESERVED = "reserved", "진행 중"
        COMPLETED = "completed", "사용 완료"
        RELEASED = "released", "차감 취소"

    class Stage(models.TextChoices):
        EXTRACT = "extract", "문항 분석"
        SOLVE = "solve", "정답·해설 생성"
        VERIFY = "verify", "독립 검산"
        BUILD = "build", "PDF 생성"
        DONE = "done", "완료"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="problem_studio_beta_runs",
        db_index=True,
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="problem_studio_beta_runs",
    )
    job_id = models.CharField(max_length=64, blank=True, default="", db_index=True)
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.RESERVED,
        db_index=True,
    )
    release_reason = models.CharField(max_length=240, blank=True, default="")
    stage = models.CharField(
        max_length=16,
        choices=Stage.choices,
        default=Stage.EXTRACT,
        db_index=True,
    )
    source_name = models.CharField(max_length=255, blank=True, default="")
    source_archive_key = models.CharField(max_length=512, blank=True, default="")
    checkpoint_key = models.CharField(max_length=512, blank=True, default="")
    solutions_key = models.CharField(max_length=512, blank=True, default="")
    result_key = models.CharField(max_length=512, blank=True, default="")
    result_filename = models.CharField(max_length=255, blank=True, default="")
    request_payload = models.JSONField(default=dict, blank=True)
    result_payload = models.JSONField(default=dict, blank=True)
    question_count = models.PositiveIntegerField(default=0)
    completed_question_count = models.PositiveIntegerField(default=0)
    verified_question_count = models.PositiveIntegerField(default=0)
    review_required_count = models.PositiveIntegerField(default=0)
    last_error = models.TextField(blank=True, default="")

    objects = TenantQuerySet.as_manager()

    class Meta:
        db_table = "problem_studio_beta_run"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["job_id"],
                condition=~models.Q(job_id=""),
                name="uq_ps_beta_run_job",
            ),
        ]
        indexes = [
            models.Index(
                fields=["tenant", "status", "created_at"],
                name="idx_ps_beta_tenant_status",
            ),
            models.Index(
                fields=["tenant", "requested_by", "created_at"],
                name="idx_ps_beta_owner_created",
            ),
        ]


class ProblemReviewReport(TimestampModel):
    """Teacher-owned, tenant-scoped draft created from an uploaded exam."""

    class Status(models.TextChoices):
        ANALYZING = "analyzing", "분석 중"
        DRAFT = "draft", "검수 초안"
        FAILED = "failed", "분석 실패"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="problem_review_reports",
        db_index=True,
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="problem_review_reports",
    )
    analysis_job_id = models.CharField(max_length=64, blank=True, default="", db_index=True)
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.ANALYZING,
        db_index=True,
    )
    title = models.CharField(max_length=200, blank=True, default="")
    source_name = models.CharField(max_length=255, blank=True, default="")
    source_summary = models.JSONField(default=dict, blank=True)
    draft = models.JSONField(default=dict, blank=True)
    version = models.PositiveIntegerField(default=1)
    last_error = models.TextField(blank=True, default="")

    objects = TenantQuerySet.as_manager()

    class Meta:
        db_table = "problem_review_report"
        ordering = ["-updated_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["analysis_job_id"],
                condition=~models.Q(analysis_job_id=""),
                name="uq_problem_review_analysis_job",
            ),
        ]
        indexes = [
            models.Index(
                fields=["tenant", "requested_by", "updated_at"],
                name="idx_problem_review_owner",
            ),
            models.Index(
                fields=["tenant", "status", "updated_at"],
                name="idx_problem_review_status",
            ),
        ]


class ProblemReviewArtifact(TimestampModel):
    """Immutable export identity for one exact reviewed report snapshot."""

    class Status(models.TextChoices):
        PENDING = "pending", "생성 중"
        READY = "ready", "다운로드 가능"
        FAILED = "failed", "생성 실패"

    class OutputFormat(models.TextChoices):
        PDF = "pdf", "PDF"
        PPTX = "pptx", "PPTX"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="problem_review_artifacts",
        db_index=True,
    )
    report = models.ForeignKey(
        ProblemReviewReport,
        on_delete=models.CASCADE,
        related_name="artifacts",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="problem_review_artifacts",
    )
    job_id = models.CharField(max_length=64, blank=True, default="", db_index=True)
    output_format = models.CharField(max_length=8, choices=OutputFormat.choices)
    report_version = models.PositiveIntegerField()
    source_fingerprint = models.CharField(max_length=64)
    status = models.CharField(
        max_length=12,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    filename = models.CharField(max_length=255, blank=True, default="")
    content_type = models.CharField(max_length=160, blank=True, default="")
    size_bytes = models.PositiveBigIntegerField(default=0)
    sha256 = models.CharField(max_length=64, blank=True, default="")
    r2_key = models.CharField(max_length=700, blank=True, default="")
    error_message = models.TextField(blank=True, default="")

    objects = TenantQuerySet.as_manager()

    class Meta:
        db_table = "problem_review_artifact"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["report", "report_version", "output_format", "source_fingerprint"],
                name="uq_problem_review_artifact_snapshot",
            ),
            models.UniqueConstraint(
                fields=["job_id"],
                condition=~models.Q(job_id=""),
                name="uq_problem_review_artifact_job",
            ),
        ]
        indexes = [
            models.Index(
                fields=["tenant", "created_by", "created_at"],
                name="idx_problem_review_art_owner",
            ),
            models.Index(
                fields=["report", "status", "created_at"],
                name="idx_problem_review_art_status",
            ),
        ]
