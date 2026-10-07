"""Student-only progress merging; staff resets keep their existing write path."""

from django.db import transaction

from apps.domains.video.models import VideoProgress
from apps.domains.video.policy import normalize_video_progress


@transaction.atomic
def update_student_video_progress(*, video, enrollment, defaults: dict):
    """Merge validated student fields under the same lock used by update_or_create.

    The unique video/enrollment key and get_or_create's locked retry also cover
    concurrent first writes. Read persisted state each time so a teacher reset
    remains authoritative. Resume position is deliberately not monotonic.
    """
    values = {
        field: defaults[field]
        for field in ("progress", "completed", "last_position")
        if field in defaults
    }
    progress, created = VideoProgress.objects.select_for_update().get_or_create(
        video=video, enrollment=enrollment, defaults=values,
    )
    if created:
        return progress, created

    if "progress" in values:
        progress.progress = max(
            normalize_video_progress(progress.progress), values["progress"],
        )
    if "completed" in values:
        progress.completed = progress.completed or values["completed"]
    if "last_position" in values:
        progress.last_position = values["last_position"]
    progress.save(update_fields=[*values, "updated_at"])
    return progress, created
