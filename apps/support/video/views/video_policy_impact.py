# PATH: apps/support/video/views/video_policy_impact.py
# PATH: apps/support/video/views/video_policy_impact.py

from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.domains.enrollment.models import Enrollment
from apps.domains.attendance.models import Attendance
from ..models import Video, VideoAccess, VideoProgress
from ..services.access_resolver import resolve_access_mode


class VideoPolicyImpactAPIView(APIView):
    """
    Admin 전용:
    특정 영상 정책이 학생들에게 어떤 영향을 주는지 미리 보기
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, video_id: int):
        video = Video.objects.select_related("session", "session__lecture").get(id=video_id)

        enrollments = Enrollment.objects.filter(
            lecture=video.session.lecture,
            status="ACTIVE",
        ).select_related("student")

        perms = {
            p.enrollment_id: p
            for p in VideoAccess.objects.filter(video=video)
        }

        progresses = {
            p.enrollment_id: p
            for p in VideoProgress.objects.filter(video=video)
        }

        attendance = {
            a.enrollment_id: a.status
            for a in Attendance.objects.filter(session=video.session)
        }

        rows = []

        for e in enrollments:
            perm = perms.get(e.id)
            prog = progresses.get(e.id)

            # Use SSOT access resolver
            access_mode = resolve_access_mode(video=video, enrollment=e)
            
            # Legacy rule for backward compatibility
            rule = perm.rule if perm else "free"
            effective = rule
            if rule == "once" and prog and prog.completed:
                effective = "free"

            rows.append({
                "enrollment": e.id,
                "student_name": e.student.name,
                "attendance_status": attendance.get(e.id),
                "rule": rule,  # Legacy field
                "effective_rule": effective,  # Legacy field
                "access_mode": access_mode.value,  # New field
                "completed": bool(prog.completed) if prog else False,
            })

        return Response(rows)
