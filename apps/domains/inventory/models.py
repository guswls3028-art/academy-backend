# PATH: apps/domains/inventory/models.py
# 멀티테넌트 인벤토리 — 폴더/파일 메타데이터 (실체는 R2)

from django.db import models
from apps.api.common.models import TimestampModel
from apps.core.models import Tenant
from apps.core.db import TenantQuerySet


class InventoryFolder(TimestampModel):
    """인벤토리 폴더 (재귀 구조)."""
    objects = TenantQuerySet.as_manager()

    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="inventory_folders",
        db_index=True,
    )
    # admin = 선생님 개인, student = 학생별 (student_ps 사용)
    scope = models.CharField(max_length=20, choices=[("admin", "선생님"), ("student", "학생")], db_index=True)
    student_ps = models.CharField(max_length=20, blank=True, default="", db_index=True)
    parent = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="children",
    )
    name = models.CharField(max_length=255)

    class Meta:
        app_label = "inventory"
        ordering = ["name"]


class InventoryFile(TimestampModel):
    """인벤토리 파일 메타데이터 (실체는 R2)."""
    objects = TenantQuerySet.as_manager()

    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="inventory_files",
        db_index=True,
    )
    scope = models.CharField(max_length=20, choices=[("admin", "선생님"), ("student", "학생")], db_index=True)
    student_ps = models.CharField(max_length=20, blank=True, default="", db_index=True)
    folder = models.ForeignKey(
        InventoryFolder,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="files",
    )
    display_name = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    icon = models.CharField(max_length=64, blank=True, default="file-text")
    # R2 객체 키 (저장 경로)
    r2_key = models.CharField(max_length=512, unique=True, db_index=True)
    original_name = models.CharField(max_length=255)
    size_bytes = models.BigIntegerField(default=0)
    content_type = models.CharField(max_length=128, default="application/octet-stream")

    class Meta:
        app_label = "inventory"
        ordering = ["-created_at"]
