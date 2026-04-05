# PATH: apps/domains/exams/models/template_bundle.py
"""
TemplateBundle — 시험/과제 템플릿 묶음

✅ 목적
- 자주 사용하는 시험 템플릿 + 과제 템플릿 조합을 묶음으로 저장
- 차시에 묶음을 적용하면 모든 항목이 일괄 생성됨

✅ 구조
- TemplateBundle: 묶음 (이름, 테넌트)
- TemplateBundleItem: 묶음 내 항목 (시험 또는 과제 템플릿 참조)
"""

from django.db import models
from apps.api.common.models import BaseModel
from apps.core.models.tenant import Tenant


class TemplateBundle(BaseModel):
    """시험/과제 템플릿 묶음"""

    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="template_bundles",
    )
    name = models.CharField(max_length=255, help_text="묶음 이름")
    description = models.TextField(blank=True, default="")

    class Meta:
        db_table = "exams_template_bundle"
        ordering = ["-updated_at"]

    def __str__(self) -> str:
        return f"TemplateBundle({self.id}, {self.name})"


class TemplateBundleItem(BaseModel):
    """묶음 내 개별 항목 — 시험 또는 과제 템플릿"""

    class ItemType(models.TextChoices):
        EXAM = "exam", "시험"
        HOMEWORK = "homework", "과제"

    bundle = models.ForeignKey(
        TemplateBundle,
        on_delete=models.CASCADE,
        related_name="items",
    )
    item_type = models.CharField(
        max_length=20,
        choices=ItemType.choices,
    )
    exam_template = models.ForeignKey(
        "exams.Exam",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="bundle_items",
        help_text="시험 템플릿 (item_type=exam 일 때)",
    )
    homework_template = models.ForeignKey(
        "homework_results.Homework",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="bundle_items",
        help_text="과제 템플릿 (item_type=homework 일 때)",
    )
    title_override = models.CharField(
        max_length=255,
        blank=True,
        default="",
        help_text="적용 시 사용할 제목 (비어있으면 템플릿 제목 사용)",
    )
    display_order = models.PositiveIntegerField(default=0)
    config = models.JSONField(
        null=True,
        blank=True,
        help_text="max_score, pass_score 등 적용 시 설정값",
    )

    class Meta:
        db_table = "exams_template_bundle_item"
        ordering = ["display_order", "id"]

    def __str__(self) -> str:
        return f"BundleItem({self.id}, {self.item_type})"
