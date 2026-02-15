# PATH: apps/support/video/services/access_resolver.py

"""
Single Source of Truth for video access mode resolution.

Business Logic (SSOT):
- Default: FREE_REVIEW (unlimited review, no monitoring, no DB session/event writes)
- PROCTORED_CLASS: Only for students with Attendance.status == "ONLINE" for that session
  - First watch = class substitute: strict policy + monitoring + audit
  - After completion (VideoProgress.completed OR VideoAccess.proctored_completed_at):
    -> automatically FREE_REVIEW for that video
- Offline students: majority, never monitored
"""

from typing import Optional

from apps.domains.enrollment.models import Enrollment
from apps.support.video.models import Video, AccessMode
from academy.adapters.db.django import repositories_video as video_repo


def resolve_access_mode(
    *,
    video: Video,
    enrollment: Enrollment,
    session_id: Optional[int] = None,
) -> AccessMode:
    """
    Resolve access mode for a video-enrollment pair (SSOT).

    Given: video, enrollment (session from video.session)
    1) BLOCKED if explicit VideoAccess override says so
    2) Attendance.status == "ONLINE" => candidate for PROCTORED_CLASS
    3) Else => FREE_REVIEW
    4) If ONLINE: check completion (VideoProgress.completed OR VideoAccess.proctored_completed_at)
       - Completed => FREE_REVIEW
       - Not completed => PROCTORED_CLASS
    """
    perm = video_repo.video_access_get(video, enrollment)

    if perm and perm.access_mode == AccessMode.BLOCKED:
        return AccessMode.BLOCKED

    session = video.session
    attendance = video_repo.attendance_filter_session_enrollment(session, enrollment).first()

    if not attendance or attendance.status != "ONLINE":
        return AccessMode.FREE_REVIEW

    progress = video_repo.video_progress_get(video, enrollment)

    if progress and progress.completed:
        return AccessMode.FREE_REVIEW

    if perm and perm.proctored_completed_at is not None:
        return AccessMode.FREE_REVIEW

    return AccessMode.PROCTORED_CLASS


def get_effective_access_mode(
    *,
    video: Video,
    enrollment: Enrollment,
    session_id: Optional[int] = None,
) -> AccessMode:
    """
    Effective access mode considering admin overrides.
    """
    perm = video_repo.video_access_get(video, enrollment)

    if perm and perm.is_override:
        return perm.access_mode

    return resolve_access_mode(
        video=video,
        enrollment=enrollment,
        session_id=session_id,
    )
