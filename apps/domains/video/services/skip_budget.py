"""Server-owned forward-skip budget for limited student video playback."""

from __future__ import annotations

from django.db import transaction

from apps.domains.video.models import VideoProgress
from apps.domains.video.policy import video_forward_skip_budget


@transaction.atomic
def consume_video_forward_skip(*, video, enrollment) -> dict:
    """Atomically grant the next fixed skip step without exceeding the video budget."""
    enrollment.__class__.objects.select_for_update().only("id").get(
        id=enrollment.id,
        tenant_id=video.tenant_id,
    )
    progress, _created = VideoProgress.objects.get_or_create(
        video=video,
        enrollment=enrollment,
    )
    budget = video_forward_skip_budget(
        duration=video.duration,
        used_seconds=progress.forward_skip_seconds_used,
    )
    granted_seconds = min(
        int(budget["step_seconds"]),
        int(budget["remaining_seconds"]),
    )
    if granted_seconds > 0:
        progress.forward_skip_seconds_used += granted_seconds
        progress.save(update_fields=["forward_skip_seconds_used", "updated_at"])
        budget = video_forward_skip_budget(
            duration=video.duration,
            used_seconds=progress.forward_skip_seconds_used,
        )

    return {**budget, "granted_seconds": granted_seconds}
