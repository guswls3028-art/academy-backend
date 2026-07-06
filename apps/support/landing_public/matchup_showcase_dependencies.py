"""Cross-domain dependencies for public matchup showcase publishing."""

from __future__ import annotations

import io
from typing import Any

from django.utils import timezone


def _snapshot_meta_from_report(report: Any, *, snapshot_at) -> dict[str, Any]:
    entries_qs = report.entries.all()
    total_entries = entries_qs.count()
    excluded = entries_qs.filter(excluded=True).count()
    counted = total_entries - excluded
    hit = 0
    for entry in entries_qs:
        if (
            not entry.excluded
            and isinstance(entry.selected_problem_ids, list)
            and len(entry.selected_problem_ids) > 0
        ):
            hit += 1
    return {
        "document_title": (report.document.title or "") if report.document_id else "",
        "document_id": report.document_id,
        "author_name": (
            report.author.name
            if report.author and getattr(report.author, "name", None)
            else (report.submitted_by_name or "")
        ),
        "report_title": report.title or "",
        "report_status": getattr(report, "status", ""),
        "total_entries": total_entries,
        "counted_entries": counted,
        "hit_count": hit,
        "hit_rate": round(hit / counted, 3) if counted else 0.0,
        "snapshot_at_iso": snapshot_at.isoformat(),
    }


def get_matchup_hit_report_for_showcase(*, tenant, hit_report_id: int):
    from apps.domains.matchup.models import MatchupHitReport

    return (
        MatchupHitReport.objects.select_related("document")
        .filter(id=hit_report_id, tenant=tenant)
        .first()
    )


def build_matchup_snapshot_for_hit_report(tenant, hit_report_id: int) -> tuple[str, int, dict[str, Any]]:
    """Generate a curated matchup PDF and persist it as an immutable showcase snapshot."""
    from apps.domains.matchup.models import MatchupHitReport
    from apps.domains.matchup.pdf_report import generate_curated_hit_report_pdf
    from apps.infrastructure.storage.r2 import upload_fileobj_to_r2_storage

    report = MatchupHitReport.objects.select_related("document", "author").get(
        id=hit_report_id,
        tenant=tenant,
    )
    pdf_bytes = generate_curated_hit_report_pdf(report)

    now = timezone.now()
    key = (
        f"matchup-showcase-snapshots/tenant_{tenant.id}/"
        f"hit_report_{report.id}/{int(now.timestamp())}.pdf"
    )
    upload_fileobj_to_r2_storage(
        fileobj=io.BytesIO(pdf_bytes),
        key=key,
        content_type="application/pdf",
    )
    return key, len(pdf_bytes), _snapshot_meta_from_report(report, snapshot_at=now)


def matchup_showcase_upload_meta_from_report(*, tenant, hit_report_id: int) -> dict[str, Any]:
    from apps.domains.matchup.models import MatchupHitReport

    report = (
        MatchupHitReport.objects.select_related("document", "author")
        .filter(id=hit_report_id, tenant=tenant)
        .first()
    )
    if not report:
        return {}
    meta = _snapshot_meta_from_report(report, snapshot_at=timezone.now())
    meta.pop("report_status", None)
    meta["source"] = "user_upload_with_ref"
    return meta
