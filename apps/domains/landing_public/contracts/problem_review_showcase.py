from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from apps.domains.landing_public.models import PublicProblemReviewShowcase


@dataclass(frozen=True)
class PublishedProblemReviewShowcase:
    id: UUID
    title: str
    status: str
    published_at: datetime
    previous_snapshot_pdf_key: str


def publish_problem_review_showcase(
    *,
    tenant,
    report_id: UUID,
    title: str,
    description: str,
    published_at: datetime,
    snapshot: dict,
    snapshot_pdf_key: str,
    snapshot_pdf_bytes: int,
    created_by,
) -> PublishedProblemReviewShowcase:
    """Upsert a reviewed public snapshot inside the caller's transaction."""

    existing = PublicProblemReviewShowcase.objects.select_for_update().filter(
        tenant=tenant,
        report_id_ref=report_id,
    ).first()
    previous_snapshot_pdf_key = existing.snapshot_pdf_key if existing else ""
    if existing:
        showcase = existing
        showcase.title = title
        showcase.description = description
        showcase.status = PublicProblemReviewShowcase.Status.PUBLISHED
        showcase.published_at = published_at
        showcase.snapshot = snapshot
        showcase.snapshot_pdf_key = snapshot_pdf_key
        showcase.snapshot_pdf_bytes = snapshot_pdf_bytes
        showcase.snapshot_at = published_at
        showcase.created_by = created_by
        showcase.save()
    else:
        showcase = PublicProblemReviewShowcase.objects.create(
            tenant=tenant,
            report_id_ref=report_id,
            title=title,
            description=description,
            status=PublicProblemReviewShowcase.Status.PUBLISHED,
            published_at=published_at,
            snapshot=snapshot,
            snapshot_pdf_key=snapshot_pdf_key,
            snapshot_pdf_bytes=snapshot_pdf_bytes,
            snapshot_at=published_at,
            created_by=created_by,
        )
    return PublishedProblemReviewShowcase(
        id=showcase.id,
        title=showcase.title,
        status=showcase.status,
        published_at=showcase.published_at,
        previous_snapshot_pdf_key=previous_snapshot_pdf_key,
    )


def hide_problem_review_showcase(*, tenant, report_id: UUID) -> bool:
    return bool(
        PublicProblemReviewShowcase.objects.filter(
            tenant=tenant,
            report_id_ref=report_id,
        ).update(status=PublicProblemReviewShowcase.Status.HIDDEN),
    )
