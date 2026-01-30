# PATH: apps/support/video/views/video_policy_impact.py
# PATH: apps/support/video/views/video_policy_impact.py

from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.domains.enrollment.models import Enrollment
from ..models import Video, VideoPermission, VideoProgress


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
            for p in VideoPermission.objects.filter(video=video)
        }

        progresses = {
            p.enrollment_id: p
            for p in VideoProgress.objects.filter(video=video)
        }

        rows = []

        for e in enrollments:
            perm = perms.get(e.id)
            prog = progresses.get(e.id)

            rule = perm.rule if perm else "free"

            effective = rule
            if rule == "once" and prog and prog.completed:
                effective = "free"

            rows.append({
                "enrollment": e.id,
                "student_name": e.student.name,
                "rule": rule,
                "effective_rule": effective,
                "completed": bool(prog.completed) if prog else False,
            })

        return Response(rows)
