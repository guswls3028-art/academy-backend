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

from apps.domains.attendance.models import Attendance
from apps.domains.enrollment.models import Enrollment
from apps.support.video.models import Video, VideoProgress, VideoAccess, AccessMode


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
    perm = VideoAccess.objects.filter(
        video=video,
        enrollment=enrollment,
    ).first()

    if perm and perm.access_mode == AccessMode.BLOCKED:
        return AccessMode.BLOCKED

    session = video.session
    attendance = Attendance.objects.filter(
        session=session,
        enrollment=enrollment,
    ).first()

    # Only ONLINE attendance gets PROCTORED_CLASS (not SUPPLEMENT per spec)
    if not attendance or attendance.status != "ONLINE":
        return AccessMode.FREE_REVIEW

    # Online: check if already completed (first watch done)
    progress = VideoProgress.objects.filter(
        video=video,
        enrollment=enrollment,
    ).first()

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
    perm = VideoAccess.objects.filter(
        video=video,
        enrollment=enrollment,
    ).first()

    if perm and perm.is_override:
        return perm.access_mode

    return resolve_access_mode(
        video=video,
        enrollment=enrollment,
        session_id=session_id,
    )
