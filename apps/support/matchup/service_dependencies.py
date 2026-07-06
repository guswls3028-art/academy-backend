"""Cross-domain dependency helpers for matchup services."""

from __future__ import annotations

from typing import Any


def dispatch_ai_job(**kwargs: Any) -> Any:
    from apps.domains.ai.gateway import dispatch_job

    return dispatch_job(**kwargs)


def inventory_file_for_r2_key(*, tenant: Any, r2_key: str) -> Any | None:
    from apps.domains.inventory.models import InventoryFile

    return InventoryFile.objects.filter(tenant=tenant, r2_key=r2_key).first()


def inventory_folder_for_matchup_backfill(
    *,
    tenant: Any,
    name: str,
    parent: Any | None,
) -> Any | None:
    from apps.domains.inventory.models import InventoryFolder

    return InventoryFolder.objects.filter(
        tenant=tenant,
        scope="admin",
        student_ps="",
        parent=parent,
        name=name,
    ).first()


def create_inventory_folder_for_matchup_backfill(
    *,
    tenant: Any,
    name: str,
    parent: Any | None,
) -> Any:
    from apps.domains.inventory.models import InventoryFolder

    return InventoryFolder.objects.create(
        tenant=tenant,
        scope="admin",
        student_ps="",
        parent=parent,
        name=name,
    )


def create_inventory_file_for_matchup_document(
    *,
    tenant: Any,
    folder: Any,
    document: Any,
) -> Any:
    from apps.domains.inventory.models import InventoryFile

    return InventoryFile.objects.create(
        tenant=tenant,
        scope="admin",
        student_ps="",
        folder=folder,
        display_name=document.title or document.original_name,
        description="",
        icon="file-text",
        r2_key=document.r2_key,
        original_name=document.original_name,
        size_bytes=document.size_bytes,
        content_type=document.content_type,
    )
