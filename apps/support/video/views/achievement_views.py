from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from django.db.models import Avg, Sum

from academy.adapters.db.django import repositories_video as video_repo
from ..models import VideoProgress


class VideoAchievementView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, video_id: int):
        video = video_repo.video_get_by_id_with_session(video_id)
        lecture = video.session.lecture

        online_attendance = video_repo.attendance_filter_session_status(video.session, "ONLINE")
        enrollment_ids = online_attendance.values_list("enrollment_id", flat=True)

        progresses = {
            p.enrollment_id: p
            for p in video_repo.video_progress_filter_video_enrollment_ids(video, enrollment_ids)
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
