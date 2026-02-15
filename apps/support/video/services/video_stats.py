# PATH: apps/support/video/services/video_stats.py

from academy.adapters.db.django import repositories_video as video_repo
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
    enrollments = video_repo.enrollment_filter_by_lecture_active(lecture)
    progresses = {p.enrollment_id: p for p in video_repo.get_video_progresses_for_video(video)}
    perms = {p.enrollment_id: p for p in video_repo.get_video_access_for_video(video)}
    attendance = {
        a.enrollment_id: a.status
        for a in video_repo.get_attendance_for_session(video.session)
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

        lecture = getattr(video.session, "lecture", None) if video.session else None
        students.append({
            "enrollment": e.id,
            "student_name": e.student.name,
            "attendance_status": attendance.get(e.id),
            "lecture_title": lecture.title if lecture else None,
            "lecture_color": getattr(lecture, "color", None) if lecture else None,
            "progress": vp.progress if vp else 0,
            "completed": vp.completed if vp else False,
            "rule": rule,  # Legacy field
            "effective_rule": effective_rule,  # Legacy field
            "access_mode": access_mode.value,  # New field
        })

    return students
