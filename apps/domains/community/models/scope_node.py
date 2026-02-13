from django.db import models
from apps.core.models import Tenant
from apps.domains.lectures.models import Lecture, Session


class ScopeNode(models.Model):
    """노출 위치 트리: COURSE(강의) > SESSION(차시). tenant 필수."""
    class Level(models.TextChoices):
        COURSE = "COURSE", "강의"
        SESSION = "SESSION", "차시"

    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="scope_nodes",
        null=False,
        db_index=True,
    )
    level = models.CharField(max_length=16, choices=Level.choices)
    lecture = models.ForeignKey(
        Lecture,
        on_delete=models.CASCADE,
        related_name="scope_nodes",
    )
    session = models.ForeignKey(
        Session,
        on_delete=models.CASCADE,
        related_name="scope_nodes",
        null=True,
        blank=True,
    )
    parent = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="children",
    )

    class Meta:
        unique_together = [("tenant", "lecture", "session")]
        indexes = [
            models.Index(fields=["tenant", "level"]),
            models.Index(fields=["tenant", "parent"]),
        ]

    def __str__(self):
        if self.session_id:
            return f"{self.lecture.title} — {self.session.title}"
        return f"{self.lecture.title} (전체)"
