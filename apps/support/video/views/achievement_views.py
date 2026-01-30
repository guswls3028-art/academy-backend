from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from django.db.models import Avg, Sum

from apps.domains.attendance.models import Attendance
from apps.domains.enrollment.models import Enrollment
from ..models import Video, VideoProgress


class VideoAchievementView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, video_id: int):
        video = Video.objects.select_related("session").get(id=video_id)
        lecture = video.session.lecture

        # 영상 수강 대상 학생 (출석이 ONLINE)
        online_attendance = Attendance.objects.filter(
            session=video.session,
            status="ONLINE",
        )

        enrollment_ids = online_attendance.values_list("enrollment_id", flat=True)

        progresses = {
            p.enrollment_id: p
            for p in VideoProgress.objects.filter(
                video=video,
                enrollment_id__in=enrollment_ids,
            )
        }

        students = []
        completed_count = 0
        total_progress = 0

        for att in online_attendance.select_related("enrollment__student"):
            enrollment = att.enrollment
            vp = progresses.get(enrollment.id)

            progress = vp.progress if vp else 0
            completed = vp.completed if vp else False

            if completed:
                completed_count += 1

            total_progress += progress

            # 상태 계산
            if progress >= 0.95:
                status = "completed"
            elif progress >= 0.5:
                status = "warning"
            else:
                status = "danger"

            students.append({
                "enrollment": enrollment.id,
                "student_name": enrollment.student.name,
                "progress": round(progress * 100, 1),
                "completed": completed,
                "watched_seconds": vp.last_position if vp else 0,
                "status": status,
            })

        total = len(students)
        avg_progress = (total_progress / total) if total else 0

        return Response({
            "summary": {
                "total_students": total,
                "avg_progress": round(avg_progress * 100, 1),
                "completed_rate": round((completed_count / total) * 100, 1) if total else 0,
                "incomplete_count": total - completed_count,
            },
            "students": students,
        })
