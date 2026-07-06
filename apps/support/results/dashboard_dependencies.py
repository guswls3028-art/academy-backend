"""Cross-domain dashboard dependencies for result endpoints."""

from __future__ import annotations

from typing import Any


def failed_video_count_since(*, tenant: Any, cutoff: Any) -> int:
    from apps.domains.video.models import Video

    return Video.objects.filter(
        tenant=tenant,
        status=Video.Status.FAILED,
        deleted_at__isnull=True,
        updated_at__gte=cutoff,
    ).count()

