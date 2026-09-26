"""Exact R2 namespace and cleanup for one Matchup analysis job."""

from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import timedelta


logger = logging.getLogger(__name__)


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


def schedule_unreferenced_analysis_artifacts(
    *, tenant_id: int, document_id: int, job_id: str,
    defer_on_list_failure: bool = True,
) -> int:
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
    try:
        keys = [
            key for obj in iter_r2_objects(bucket=settings.R2_STORAGE_BUCKET, prefix=prefix)
            if (key := obj.get("Key")) and key.startswith(prefix)
            and ("/problems/" in key or "/pages/" in key)
        ]
    except Exception:
        if not defer_on_list_failure:
            raise
        schedule_artifact_scan_intent(
            tenant_id=tenant_id, document_id=document_id, job_id=job_id,
        )
        logger.warning(
            "MATCHUP_ARTIFACT_SCAN_DEFERRED | tenant=%s | doc=%s | job=%s",
            tenant_id, document_id, job_id, exc_info=True,
        )
        return 0
    if not keys:
        return 0
    with transaction.atomic():
        return len(schedule_inventory_storage_cleanup(tenant_id=tenant_id, object_keys=keys))


def schedule_artifact_scan_intent(*, tenant_id: int, document_id: int, job_id: str) -> int:
    """Persist a retryable discovery request outside the old storage outbox."""
    from django.db import transaction
    from apps.domains.matchup.models import MatchupArtifactScanIntent

    if int(tenant_id) <= 0 or int(document_id) <= 0 or not job_id or len(job_id) > 64:
        raise ValueError("Matchup artifact scan requires an exact tenant, document and job")
    with transaction.atomic():
        intent, created = MatchupArtifactScanIntent.objects.select_for_update().get_or_create(
            tenant_id=tenant_id, document_id=document_id, job_id=job_id,
        )
        if not created and intent.status == MatchupArtifactScanIntent.Status.CLEANED:
            intent.status = MatchupArtifactScanIntent.Status.PENDING
            intent.cleaned_at = None
            intent.last_error = ""
            intent.save(update_fields=["status", "cleaned_at", "last_error", "updated_at"])
        transaction.on_commit(lambda intent_id=intent.id: _process_artifact_scan_safely(intent_id))
        return intent.id


def _process_artifact_scan_safely(intent_id: int) -> None:
    try:
        process_artifact_scan_intents(intent_ids=[intent_id])
    except Exception:
        logger.exception("Matchup artifact scan callback failed before intent processing")


def process_artifact_scan_intents(*, intent_ids=None, limit: int = 100) -> dict[str, int]:
    """Retry job-owned listings; exact object deletion remains in the storage outbox."""
    from django.db import transaction
    from django.db.models import Q
    from django.utils import timezone

    from apps.domains.matchup.models import MatchupArtifactScanIntent
    from apps.support.matchup.service_dependencies import matchup_analysis_job_exists

    stale_before = timezone.now() - timedelta(minutes=15)
    queryset = MatchupArtifactScanIntent.objects.filter(
        Q(status__in=[
            MatchupArtifactScanIntent.Status.PENDING,
            MatchupArtifactScanIntent.Status.FAILED,
        ]) | Q(
            status=MatchupArtifactScanIntent.Status.PROCESSING,
            last_attempt_at__lt=stale_before,
        ) | Q(
            status=MatchupArtifactScanIntent.Status.PROCESSING,
            last_attempt_at__isnull=True,
        )
    ).order_by("id")
    if intent_ids is not None:
        queryset = queryset.filter(id__in=tuple(dict.fromkeys(int(value) for value in intent_ids)))
    selected_ids = list(queryset.values_list("id", flat=True)[:max(1, min(limit, 1000))])
    counts = {"cleaned": 0, "failed": 0}
    for intent_id in selected_ids:
        attempted_at = timezone.now()
        claim_token = uuid.uuid4()
        with transaction.atomic():
            intent = MatchupArtifactScanIntent.objects.select_for_update().filter(id=intent_id).first()
            if not intent or intent.status == MatchupArtifactScanIntent.Status.CLEANED:
                continue
            if (intent.status == MatchupArtifactScanIntent.Status.PROCESSING
                    and intent.last_attempt_at and intent.last_attempt_at >= attempted_at - timedelta(minutes=15)):
                continue
            intent.status = MatchupArtifactScanIntent.Status.PROCESSING
            intent.attempt_count += 1
            intent.claim_token = claim_token
            intent.last_attempt_at = attempted_at
            intent.last_error = ""
            intent.save(update_fields=[
                "status", "attempt_count", "claim_token", "last_attempt_at", "last_error", "updated_at",
            ])
        try:
            if not matchup_analysis_job_exists(
                tenant_id=intent.tenant_id, document_id=intent.document_id, job_id=intent.job_id,
            ):
                raise ValueError("Matchup artifact scan job ownership cannot be verified")
            schedule_unreferenced_analysis_artifacts(
                tenant_id=intent.tenant_id, document_id=intent.document_id,
                job_id=intent.job_id, defer_on_list_failure=False,
            )
        except Exception:
            logger.warning("Matchup artifact scan failed intent_id=%s", intent_id, exc_info=True)
            outcome = MatchupArtifactScanIntent.Status.FAILED
        else:
            outcome = MatchupArtifactScanIntent.Status.CLEANED
        with transaction.atomic():
            updated = MatchupArtifactScanIntent.objects.filter(
                id=intent_id, status=MatchupArtifactScanIntent.Status.PROCESSING,
                claim_token=claim_token,
            ).update(
                status=outcome,
                claim_token=None,
                last_attempt_at=attempted_at,
                last_error="" if outcome == MatchupArtifactScanIntent.Status.CLEANED else "artifact_scan_failed",
                cleaned_at=attempted_at if outcome == MatchupArtifactScanIntent.Status.CLEANED else None,
                updated_at=attempted_at,
            )
        if updated:
            counts[outcome] += 1
    return counts


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
