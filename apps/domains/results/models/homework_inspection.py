from django.db import models


class HomeworkInspection(models.Model):
    """Practice quantity and explicitly shared feedback, independent of grading."""

    tenant = models.ForeignKey("core.Tenant", on_delete=models.CASCADE)
    session = models.ForeignKey("lectures.Session", on_delete=models.CASCADE)
    homework = models.ForeignKey("homework_results.Homework", on_delete=models.CASCADE)
    enrollment = models.ForeignKey("enrollment.Enrollment", on_delete=models.CASCADE)
    solved = models.PositiveIntegerField(null=True)
    total = models.PositiveIntegerField(null=True)
    feedback = models.CharField(max_length=2000, blank=True, default="")
    version = models.PositiveIntegerField(default=0)
    updated_by = models.ForeignKey("core.User", null=True, on_delete=models.SET_NULL)
    updated_at = models.DateTimeField(auto_now=True)

    # The shared assessment visibility check uses these attributes for both kinds.
    assessment_kind = "homework"

    @property
    def assessment_id(self):
        return self.homework_id

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["tenant", "session", "homework", "enrollment"], name="unique_homework_inspection"),
        ]
