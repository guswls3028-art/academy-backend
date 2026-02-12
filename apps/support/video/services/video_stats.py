# PATH: apps/support/video/services/video_stats.py

from apps.domains.enrollment.models import Enrollment
from apps.domains.attendance.models import Attendance
from apps.support.video.models import VideoProgress, VideoAccess, Video
from apps.support.video.services.access_resolver import resolve_access_mode


def build_video_stats_students(video):
    """
    ✅ Single Source of Truth
    - stats
    - policy-impact
    - admin preview
    전부 이 함수만 사용해야 함
    """

    lecture = video.session.lecture

    enrollments = Enrollment.objects.filter(
        lecture=lecture,
        status="ACTIVE",
    ).select_related("student")

    progresses = {
        p.enrollment_id: p
        for p in VideoProgress.objects.filter(video=video)
    }

    perms = {
        p.enrollment_id: p
        for p in VideoAccess.objects.filter(video=video)
    }

    attendance = {
        a.enrollment_id: a.status
        for a in Attendance.objects.filter(session=video.session)
    }

    students = []

    for e in enrollments:
        vp = progresses.get(e.id)
        perm = perms.get(e.id)

        # Use SSOT access resolver
        access_mode = resolve_access_mode(video=video, enrollment=e)
        
        # Legacy rule for backward compatibility
        rule = perm.rule if perm else "free"
        effective_rule = rule
        if rule == "once" and vp and vp.completed:
            effective_rule = "free"

        students.append({
            "enrollment": e.id,
            "student_name": e.student.name,
            "attendance_status": attendance.get(e.id),
            "progress": vp.progress if vp else 0,
            "completed": vp.completed if vp else False,
            "rule": rule,  # Legacy field
            "effective_rule": effective_rule,  # Legacy field
            "access_mode": access_mode.value,  # New field
        })

    return students
