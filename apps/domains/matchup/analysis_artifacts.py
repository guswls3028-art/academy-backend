"""Exact R2 namespace and cleanup for one Matchup analysis job."""

from __future__ import annotations

import hashlib


def analysis_artifact_prefix(*, tenant_id: int | str, job_id: str) -> str:
    tenant = int(tenant_id)
    if tenant <= 0 or not job_id:
        raise ValueError("Matchup artifact scope requires tenant and job")
    job_hash = hashlib.sha256(str(job_id).encode("utf-8")).hexdigest()
    return f"tenants/{tenant}/matchup/jobs/{job_hash}/"


def detached_auto_image_keys(rows) -> list[str]:
    """Collect crop and generated public image keys from detached auto rows."""
    keys = []
    for _, image_key, meta in rows:
        if image_key:
            keys.append(image_key)
        cleanup = meta.get("public_cleanup") if isinstance(meta, dict) else None
        if isinstance(cleanup, dict) and cleanup.get("public_image_key"):
            keys.append(cleanup["public_image_key"])
    return keys


def schedule_unreferenced_analysis_artifacts(*, tenant_id: int, document_id: int, job_id: str) -> int:
    """Queue only unreferenced objects in this exact job's R2 namespace."""
    from django.conf import settings
    from django.db import transaction

    from academy.adapters.storage.r2_objects import iter_r2_objects
    from apps.support.inventory.storage_cleanup_dependencies import schedule_inventory_storage_cleanup
    from apps.support.matchup.service_dependencies import matchup_analysis_job_exists

    if not matchup_analysis_job_exists(
        tenant_id=tenant_id, document_id=document_id, job_id=job_id,
    ):
        return 0  # Legacy/test result without a verified job cannot own R2 objects.

    prefix = analysis_artifact_prefix(tenant_id=tenant_id, job_id=job_id)
    keys = [
        key for obj in iter_r2_objects(bucket=settings.R2_STORAGE_BUCKET, prefix=prefix)
        if (key := obj.get("Key")) and key.startswith(prefix)
        and ("/problems/" in key or "/pages/" in key)
    ]
    if not keys:
        return 0
    with transaction.atomic():
        return len(schedule_inventory_storage_cleanup(tenant_id=tenant_id, object_keys=keys))


def schedule_detached_auto_images(*, tenant_id: int, image_keys: list[str]) -> int:
    """Retire detached automatic crops; never accept another tenant or namespace."""
    from django.db import transaction

    from apps.support.inventory.storage_cleanup_dependencies import schedule_inventory_storage_cleanup

    prefix = f"tenants/{tenant_id}/matchup/"
    keys = [key for key in image_keys if isinstance(key, str) and key.startswith(prefix)]
    if not keys:
        return 0
    with transaction.atomic():
        return len(schedule_inventory_storage_cleanup(tenant_id=tenant_id, object_keys=keys))
