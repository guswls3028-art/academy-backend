from django.db import models

from apps.core.models.base import TimestampModel


class PublicProblemReviewShowcase(TimestampModel):
    """Teacher-reviewed problem analysis published as a public snapshot."""

    class Status(models.TextChoices):
        PUBLISHED = "published", "공개"
        HIDDEN = "hidden", "비공개"

    tenant = models.ForeignKey(
        "core.Tenant",
        on_delete=models.CASCADE,
        related_name="public_problem_review_showcases",
        db_index=True,
    )
    report_id_ref = models.UUIDField(db_index=True)
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True, default="")
    status = models.CharField(
        max_length=12,
        choices=Status.choices,
        default=Status.PUBLISHED,
        db_index=True,
    )
    published_at = models.DateTimeField(null=True, blank=True, db_index=True)
    snapshot = models.JSONField(default=dict, blank=True)
    snapshot_pdf_key = models.CharField(max_length=512, blank=True, default="")
    snapshot_pdf_bytes = models.PositiveIntegerField(default=0)
    snapshot_at = models.DateTimeField(null=True, blank=True, db_index=True)
    created_by = models.ForeignKey(
        "core.User",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_public_problem_review_showcases",
    )
    view_count = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "landing_public_problem_review_showcase"
        ordering = ["-published_at", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "report_id_ref"],
                name="lp_problem_review_tenant_report_uniq",
            ),
        ]
        indexes = [
            models.Index(
                fields=["tenant", "status", "-published_at"],
                name="lp_problem_review_public_idx",
            ),
        ]

    def __str__(self):
        return f"PublicProblemReviewShowcase(tenant={self.tenant_id}, id={self.pk})"
