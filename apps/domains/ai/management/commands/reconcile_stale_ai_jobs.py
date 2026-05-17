from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.domains.ai.models import AIJobModel
from apps.domains.matchup.models import MatchupDocument


TERMINAL_REASON_PREFIX = "stale_running_reconciled"
DEFAULT_TERMINAL_SOURCE_STATUSES = {"done", "failed"}


@dataclass(frozen=True)
class ReconcileCandidate:
    job_id: str
    source_id: str | None
    reason: str


def _is_stale(job: AIJobModel, cutoff) -> bool:
    lease = job.lease_expires_at
    reference = lease or job.updated_at or job.created_at
    return reference <= cutoff


def iter_stale_matchup_candidates(
    *,
    older_than_hours: int,
    limit: int,
    terminal_source_statuses: Iterable[str] = DEFAULT_TERMINAL_SOURCE_STATUSES,
) -> list[ReconcileCandidate]:
    cutoff = timezone.now() - timezone.timedelta(hours=older_than_hours)
    terminal_statuses = {str(s).lower() for s in terminal_source_statuses}
    candidates: list[ReconcileCandidate] = []

    qs = (
        AIJobModel.objects
        .filter(status="RUNNING", source_domain="matchup", job_type="matchup_analysis")
        .order_by("created_at", "id")
    )
    for job in qs.iterator():
        if len(candidates) >= limit:
            break
        if not _is_stale(job, cutoff):
            continue

        source_id = str(job.source_id or "")
        if not source_id.isdigit():
            candidates.append(ReconcileCandidate(job.job_id, job.source_id, "invalid_source_id"))
            continue

        doc = MatchupDocument.objects.filter(id=int(source_id)).only("id", "status", "ai_job_id").first()
        if doc is None:
            candidates.append(ReconcileCandidate(job.job_id, source_id, "orphan_source"))
            continue

        current_job_id = str(doc.ai_job_id or "")
        if current_job_id and current_job_id != str(job.job_id) and str(doc.status).lower() in terminal_statuses:
            candidates.append(ReconcileCandidate(job.job_id, source_id, f"superseded_source:{doc.status}"))

    return candidates


def reconcile_candidates(candidates: Iterable[ReconcileCandidate], *, execute: bool) -> int:
    if not execute:
        return 0

    updated = 0
    now = timezone.now()
    for candidate in candidates:
        error = f"{TERMINAL_REASON_PREFIX}:{candidate.reason}"
        with transaction.atomic():
            job = AIJobModel.objects.select_for_update().filter(job_id=candidate.job_id, status="RUNNING").first()
            if not job:
                continue
            job.status = "FAILED"
            job.error_message = error
            job.last_error = error
            job.locked_by = None
            job.locked_at = None
            job.lease_expires_at = None
            job.updated_at = now
            job.save(update_fields=[
                "status",
                "error_message",
                "last_error",
                "locked_by",
                "locked_at",
                "lease_expires_at",
                "updated_at",
            ])
            updated += 1
    return updated


class Command(BaseCommand):
    help = "Reconcile stale RUNNING matchup AI jobs that no longer own their source document."

    def add_arguments(self, parser):
        parser.add_argument("--older-than-hours", type=int, default=24)
        parser.add_argument("--limit", type=int, default=100)
        parser.add_argument("--execute", action="store_true")

    def handle(self, *args, **options):
        older_than_hours = int(options["older_than_hours"])
        limit = int(options["limit"])
        execute = bool(options["execute"])

        candidates = iter_stale_matchup_candidates(
            older_than_hours=older_than_hours,
            limit=limit,
        )
        for candidate in candidates:
            self.stdout.write(
                f"{'[EXEC]' if execute else '[DRY]'} "
                f"job_id={candidate.job_id} source_id={candidate.source_id} reason={candidate.reason}"
            )

        updated = reconcile_candidates(candidates, execute=execute)
        self.stdout.write(
            self.style.SUCCESS(
                f"stale_ai_jobs candidates={len(candidates)} updated={updated} execute={execute}"
            )
        )
