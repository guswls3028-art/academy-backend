# PATH: apps/domains/results/views/admin_session_exams_view.py
"""
Admin Session → Exams 조회

GET /results/admin/sessions/{session_id}/exams/

⚠️ 현재 계약:
- Session.exam = ForeignKey(Exam) (단일)
- 응답은 리스트 형태로 고정 (미래 다중 시험 대비)
"""

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from apps.domains.results.permissions import IsTeacherOrAdmin
from apps.domains.lectures.models import Session


class AdminSessionExamsView(APIView):
    permission_classes = [IsAuthenticated, IsTeacherOrAdmin]

    def get(self, request, session_id: int):
        session = Session.objects.filter(id=int(session_id)).first()
        if not session or not getattr(session, "exam_id", None):
            return Response([])

        exam = session.exam

        return Response([
            {
                "exam_id": int(exam.id),
                "title": getattr(exam, "title", ""),
                "open_at": getattr(exam, "open_at", None),
                "close_at": getattr(exam, "close_at", None),
                "allow_retake": bool(getattr(exam, "allow_retake", False)),
                "max_attempts": int(getattr(exam, "max_attempts", 1) or 1),
            }
        ])
