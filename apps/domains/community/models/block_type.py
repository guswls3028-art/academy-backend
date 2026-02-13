from django.db import models
from apps.core.models import Tenant


class BlockType(models.Model):
    """공지/질의/오탈자/숙제 등 블록 타입. tenant 필수."""
    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="community_block_types",
        null=False,
        db_index=True,
    )
    code = models.CharField(max_length=32)
    label = models.CharField(max_length=64)
    order = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ["order", "id"]
        unique_together = [("tenant", "code")]

    def __str__(self):
        return f"[{self.tenant_id}] {self.label}"
