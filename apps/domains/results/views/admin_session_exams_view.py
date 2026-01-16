# PATH: apps/domains/results/views/admin_session_exams_view.py
"""
Admin Session → Exams 조회

GET /results/admin/sessions/{session_id}/exams/

✅ 현재 계약(리팩토링 완료):
- Session 1 : Exam N
- canonical relation: exams.Exam.sessions (ManyToManyField to lectures.Session)

응답은 리스트 형태로 고정:
[
  { exam_id, title, open_at, close_at, allow_retake, max_attempts },
  ...
]
"""

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from apps.domains.results.permissions import IsTeacherOrAdmin
from apps.domains.lectures.models import Session
from apps.domains.exams.models import Exam


class AdminSessionExamsView(APIView):
    permission_classes = [IsAuthenticated, IsTeacherOrAdmin]

    @staticmethod
    def _get_exams_for_session(session: Session) -> list[Exam]:
        """
        Session에 연결된 Exam 목록 조회

        ✅ canonical:
        - session.exams (Exam.sessions related_name="exams")

        방어적 fallback:
        - Exam.objects.filter(sessions=session)
        """
        if hasattr(session, "exams"):
            try:
                return list(session.exams.all())
            except Exception:
                pass
        return list(Exam.objects.filter(sessions__id=int(session.id)).distinct())

    def get(self, request, session_id: int):
        session = Session.objects.filter(id=int(session_id)).first()
        if not session:
            return Response([])

        exams = self._get_exams_for_session(session)
        if not exams:
            return Response([])

        return Response([
            {
                "exam_id": int(exam.id),
                "title": getattr(exam, "title", "") or "",
                "open_at": getattr(exam, "open_at", None),
                "close_at": getattr(exam, "close_at", None),
                "allow_retake": bool(getattr(exam, "allow_retake", False)),
                "max_attempts": int(getattr(exam, "max_attempts", 1) or 1),
            }
            for exam in exams
        ])
