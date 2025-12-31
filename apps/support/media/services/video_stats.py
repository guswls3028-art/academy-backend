# apps/support/media/services/video_stats.py

from apps.domains.enrollment.models import Enrollment
from apps.domains.attendance.models import Attendance
from apps.support.media.models import VideoProgress, VideoPermission


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
        for p in VideoPermission.objects.filter(video=video)
    }

    attendance = {
        a.enrollment_id: a.status
        for a in Attendance.objects.filter(session=video.session)
    }

    students = []

    for e in enrollments:
        vp = progresses.get(e.id)
        perm = perms.get(e.id)

        rule = perm.rule if perm else "free"
        effective_rule = rule

        # once → completed 시 free 승격
        if rule == "once" and vp and vp.completed:
            effective_rule = "free"

        students.append({
            "enrollment": e.id,
            "student_name": e.student.name,
            "attendance_status": attendance.get(e.id),
            "progress": vp.progress if vp else 0,
            "completed": vp.completed if vp else False,
            "rule": rule,
            "effective_rule": effective_rule,
        })

    return students
