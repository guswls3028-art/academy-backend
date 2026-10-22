from django.db import models
from django.core.validators import MaxLengthValidator


class ParentAssessmentComment(models.Model):
    """Explicit parent-facing assessment feedback; never populated from internal memos."""

    tenant = models.ForeignKey("core.Tenant", on_delete=models.CASCADE)
    session = models.ForeignKey("lectures.Session", on_delete=models.CASCADE)
    enrollment = models.ForeignKey("enrollment.Enrollment", on_delete=models.CASCADE)
    assessment_kind = models.CharField(max_length=8, choices=[("exam", "Exam"), ("homework", "Homework")])
    assessment_id = models.PositiveBigIntegerField()
    text = models.TextField(blank=True, default="", validators=[MaxLengthValidator(2000)])
    version = models.PositiveIntegerField(default=0)
    updated_by = models.ForeignKey("core.User", null=True, on_delete=models.SET_NULL)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["tenant", "session", "enrollment", "assessment_kind", "assessment_id"], name="uniq_parent_assessment_comment")]


class ParentAssessmentCommentRevision(models.Model):
    comment = models.ForeignKey(ParentAssessmentComment, on_delete=models.CASCADE, related_name="revisions")
    version = models.PositiveIntegerField()
    text = models.TextField()
    author = models.ForeignKey("core.User", null=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["comment", "version"], name="uniq_parent_comment_revision")]
        ordering = ["-version"]
