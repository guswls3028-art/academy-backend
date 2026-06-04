
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.core.permissions import TenantResolvedAndStaff

from academy.adapters.db.django import repositories_video as video_repo
from ..policy import is_video_progress_complete
from ..services.access_resolver import resolve_access_modes_prefetched


class VideoPolicyImpactAPIView(APIView):
    """
    Admin 전용:
    특정 영상 정책이 학생들에게 어떤 영향을 주는지 미리 보기
    """

    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def get(self, request, video_id: int):
        video = video_repo.video_get_by_id_with_relations(video_id)
        enrollments = video_repo.enrollment_filter_by_lecture_active(video.session.lecture)
        perms = {p.enrollment_id: p for p in video_repo.get_video_access_for_video(video)}
        progresses = {p.enrollment_id: p for p in video_repo.get_video_progresses_for_video(video)}
        attendance = {
            a.enrollment_id: a.status
            for a in video_repo.get_attendance_for_session(video.session)
        }
        access_modes = resolve_access_modes_prefetched(
            video=video,
            enrollments=enrollments,
            progresses_by_enrollment_id=progresses,
            access_by_enrollment_id=perms,
            attendance_status_by_enrollment_id=attendance,
        )

        rows = []

        for e in enrollments:
            perm = perms.get(e.id)
            prog = progresses.get(e.id)
            access_mode = access_modes[e.id]
            completed = (
                is_video_progress_complete(prog.progress, prog.completed)
                if prog
                else False
            )

            # Legacy rule for backward compatibility
            rule = perm.rule if perm else "free"
            effective = rule
            if rule == "once" and completed:
                effective = "free"

            lecture = getattr(video.session, "lecture", None) if video.session else None
            rows.append({
                "enrollment": e.id,
                "student_name": e.student.name,
                "attendance_status": attendance.get(e.id),
                "lecture_title": lecture.title if lecture else None,
                "lecture_color": getattr(lecture, "color", None) if lecture else None,
                "rule": rule,  # Legacy field
                "effective_rule": effective,  # Legacy field
                "access_mode": access_mode.value,  # New field
                "completed": completed,
            })

        return Response(rows)
