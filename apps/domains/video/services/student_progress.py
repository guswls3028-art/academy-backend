"""Student-only progress merging; staff resets keep their existing write path."""

from django.db import transaction

from apps.domains.video.models import Video, VideoProgress
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
    if video.visibility == Video.Visibility.PUBLIC:
        from rest_framework.exceptions import PermissionDenied
        from academy.adapters.db.django import repositories_video as video_repo
        from apps.domains.video.models import AccessMode

        # Serialize first public progress with an ordinary enrollment created at the same time.
        student = video_repo.lock_direct_video_student(tenant=video.tenant, student_id=enrollment.student_id)
        current = video_repo.get_video_for_update(video.pk)
        if student is None or current is None or current.visibility != Video.Visibility.PUBLIC or current.tenant_id != student.tenant_id:
            raise PermissionDenied("공개 영상 권한이 변경되었습니다.")
        enrollment = video_repo.public_video_record_candidates(
            current, tenant_id=student.tenant_id, student_id=student.pk,
        ).first()
        if enrollment is None:
            raise PermissionDenied("공개 영상 수강 정보를 확인할 수 없습니다.")
        permission = video_repo.video_access_get(current, enrollment)
        if permission and (permission.access_mode == AccessMode.BLOCKED or permission.rule == "blocked"):
            raise PermissionDenied("이 영상은 시청이 제한되었습니다.")
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
