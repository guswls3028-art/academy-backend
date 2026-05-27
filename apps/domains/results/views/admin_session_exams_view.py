"""
Admin Session → Exams 조회

GET /results/admin/sessions/{session_id}/exams/

✅ 현재 계약(리팩토링 완료):
- Session 1 : Exam N
- canonical relation: exams.Exam.sessions (ManyToManyField to lectures.Session)

응답은 리스트 형태로 고정:
[
  {
    exam_id,
    title,
    exam_type,
    open_at,
    close_at,
    allow_retake,
    max_attempts,
    display_order
  },
  ...
]
"""

from django.utils.timezone import localtime
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from apps.domains.results.permissions import IsTeacherOrAdmin
from apps.domains.lectures.models import Session
from apps.domains.results.utils.session_exam import get_exams_for_session


def _dt(v):
    """datetime → ISO string | None (프론트 계약 고정)"""
    return localtime(v).isoformat() if v else None


class AdminSessionExamsView(APIView):
    permission_classes = [IsAuthenticated, IsTeacherOrAdmin]

    def get(self, request, session_id: int):
        # ✅ tenant isolation: verify session belongs to tenant
        session = Session.objects.filter(id=int(session_id), lecture__tenant=request.tenant).first()
        if not session:
            return Response([])

        exams = list(get_exams_for_session(session).filter(tenant=request.tenant))
        if not exams:
            return Response([])

        return Response([
            {
                "exam_id": int(exam.id),
                "title": exam.title or "",
                "exam_type": exam.exam_type,
                "open_at": _dt(exam.open_at),
                "close_at": _dt(exam.close_at),
                "allow_retake": bool(exam.allow_retake),
                "max_attempts": int(exam.max_attempts),
                "display_order": int(getattr(exam, "display_order", 0) or 0),
            }
            for exam in exams
        ])
