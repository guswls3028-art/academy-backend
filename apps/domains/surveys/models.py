from django.conf import settings
from django.db import models

from apps.core.db import TenantQuerySet
from apps.core.models.base import TimestampModel


class Survey(TimestampModel):
    class Kind(models.TextChoices):
        GENERAL = "general", "일반 설문"
        SCHOOL_SCORE = "school_score", "성적 취합"
        MEMO = "memo", "학생 메모"

    class Status(models.TextChoices):
        DRAFT = "draft", "작성 중"
        OPEN = "open", "응답 받는 중"
        CLOSED = "closed", "마감"

    objects = TenantQuerySet.as_manager()
    tenant = models.ForeignKey("core.Tenant", on_delete=models.CASCADE)
    title = models.CharField(max_length=120)
    description = models.TextField(blank=True, default="")
    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.GENERAL)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    questions = models.JSONField(default=list)
    audience = models.JSONField(default=dict)
    score_defaults = models.JSONField(default=dict)
    allow_attachments = models.BooleanField(default=False)
    opens_at = models.DateTimeField(null=True, blank=True)
    closes_at = models.DateTimeField(null=True, blank=True)
    response_limit = models.PositiveIntegerField(null=True, blank=True)
    published_at = models.DateTimeField(null=True, blank=True)
    version = models.PositiveIntegerField(default=1)
    creation_key = models.UUIDField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)

    class Meta:
        db_table = "surveys_survey"
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["tenant", "status", "-created_at"], name="survey_tenant_status_idx")]
        constraints = [
            models.CheckConstraint(condition=models.Q(response_limit__isnull=True) | models.Q(response_limit__gt=0), name="survey_positive_capacity"),
            models.UniqueConstraint(fields=["tenant", "creation_key"], name="survey_create_retry_unique"),
        ]


class SurveyRecipient(TimestampModel):
    objects = TenantQuerySet.as_manager()
    tenant = models.ForeignKey("core.Tenant", on_delete=models.CASCADE)
    survey = models.ForeignKey(Survey, on_delete=models.CASCADE, related_name="recipients")
    student = models.ForeignKey("students.Student", on_delete=models.CASCADE, related_name="survey_invitations")

    class Meta:
        db_table = "surveys_recipient"
        constraints = [models.UniqueConstraint(fields=["survey", "student"], name="survey_recipient_unique")]
        indexes = [models.Index(fields=["tenant", "student", "survey"], name="survey_student_inbox_idx")]


class SurveyResponse(TimestampModel):
    objects = TenantQuerySet.as_manager()
    tenant = models.ForeignKey("core.Tenant", on_delete=models.CASCADE)
    survey = models.ForeignKey(Survey, on_delete=models.CASCADE, related_name="responses")
    student = models.ForeignKey("students.Student", on_delete=models.CASCADE, related_name="survey_responses")
    submitted_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    answers = models.JSONField(default=dict)
    version = models.PositiveIntegerField(default=1)
    attachments = models.ManyToManyField("inventory.InventoryFile", related_name="survey_responses", blank=True)
    score_submissions = models.ManyToManyField("results.StudentReportedScore", related_name="survey_responses", blank=True)

    class Meta:
        db_table = "surveys_response"
        ordering = ["-updated_at", "-id"]
        constraints = [models.UniqueConstraint(fields=["survey", "student"], name="survey_response_unique")]
        indexes = [models.Index(fields=["tenant", "student", "-updated_at"], name="survey_student_history_idx")]
