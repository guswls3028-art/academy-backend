# PATH: apps/core/models/landing_consult.py
#
# 학원 홈페이지 상담 요청 폼 — 외부 학부모/학생이 contact 섹션 form으로 제출.
# 학원장은 어드민에서 수신함 확인.

from django.db import models
from apps.core.models.base import TimestampModel


class LandingConsultRequest(TimestampModel):
    """
    공개 랜딩 상담 요청.

    - tenant: 어느 학원에 들어온 요청인지
    - name / phone: 학부모/학생 연락처
    - interest: 관심 강좌/학년 (선택)
    - message: 자유 메시지
    - read_at: 학원장이 확인한 시점 (null = 미확인)
    - source: 어느 페이지/섹션에서 들어왔는지 (메인 contact / 갤러리 등)
    """

    tenant = models.ForeignKey(
        "core.Tenant",
        on_delete=models.CASCADE,
        related_name="landing_consult_requests",
        db_index=True,
    )
    name = models.CharField(max_length=50)
    phone = models.CharField(max_length=20)
    interest = models.CharField(max_length=80, blank=True)
    message = models.TextField(blank=True)
    source = models.CharField(max_length=40, default="landing", help_text="제출 출처(landing/reports/...)")
    read_at = models.DateTimeField(null=True, blank=True)
    # 학원장이 처리 메모 — 외부 비공개
    admin_memo = models.TextField(blank=True)

    class Meta:
        db_table = "core_landing_consult_request"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["tenant", "-created_at"]),
            models.Index(fields=["tenant", "read_at"]),
        ]

    def __str__(self):
        return f"ConsultRequest(tenant={self.tenant_id}, name={self.name}, created={self.created_at:%Y-%m-%d})"
