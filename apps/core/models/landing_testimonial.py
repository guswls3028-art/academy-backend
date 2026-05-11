# PATH: apps/core/models/landing_testimonial.py
#
# 학원 홈페이지 수강생 후기 제출 — 학생/학부모가 직접 testimonial 제출.
# 학원장 승인(approved) 후에만 공개 testimonials 섹션에 노출.

from django.db import models
from apps.core.models.base import TimestampModel


class LandingTestimonialSubmission(TimestampModel):
    """공개 후기 제출.

    - status: pending(승인 대기) / approved(공개 노출) / rejected(거절)
    - approved_at + reviewed_by: 승인 audit
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    tenant = models.ForeignKey(
        "core.Tenant",
        on_delete=models.CASCADE,
        related_name="landing_testimonials",
        db_index=True,
    )
    name = models.CharField(max_length=50)
    role = models.CharField(max_length=80, blank=True, help_text="학년/관계 등 (예: 고1 학부모)")
    text = models.TextField()
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING, db_index=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(
        "core.User",
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="reviewed_testimonials",
    )

    class Meta:
        db_table = "core_landing_testimonial"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["tenant", "status", "-created_at"]),
        ]

    def __str__(self):
        return f"Testimonial(tenant={self.tenant_id}, name={self.name}, status={self.status})"
