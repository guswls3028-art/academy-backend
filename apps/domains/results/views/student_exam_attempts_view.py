# PATH: apps/domains/results/views/student_exam_attempts_view.py
"""
Student Exam Attempt History

GET /results/me/exams/{exam_id}/attempts/

- 학생 본인 enrollment 기준
- 재시험 히스토리 UI 전용
"""

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from apps.domains.results.permissions import IsStudent
from apps.domains.results.models import ExamAttempt
from apps.domains.enrollment.models import Enrollment


class MyExamAttemptsView(APIView):
    permission_classes = [IsAuthenticated, IsStudent]

    def get(self, request, exam_id: int):
        user = request.user

        # enrollment 탐색 (방어)
        qs = Enrollment.objects.all()
        if hasattr(Enrollment, "user_id"):
            qs = qs.filter(user_id=user.id)
        elif hasattr(Enrollment, "student_id"):
            qs = qs.filter(student_id=user.id)

        enrollment = qs.first()
        if not enrollment:
            return Response([])

        attempts = ExamAttempt.objects.filter(
            exam_id=int(exam_id),
            enrollment_id=int(enrollment.id),
        ).order_by("attempt_index")

        return Response([
            {
                "attempt_id": a.id,
                "attempt_index": a.attempt_index,
                "is_retake": a.is_retake,
                "is_representative": a.is_representative,
                "status": a.status,
                "created_at": a.created_at,
            }
            for a in attempts
        ])
