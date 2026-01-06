# PATH: apps/domains/exams/views/exam_view.py

from rest_framework.viewsets import ModelViewSet
from rest_framework.permissions import IsAuthenticated

from apps.domains.exams.models import Exam
from apps.domains.exams.serializers.exam import ExamSerializer


class ExamViewSet(ModelViewSet):
    """
    ✅ SaaS 표준 Exam 조회 API

    지원:
    - GET /exams/?session_id=123
    - GET /exams/?lecture_id=10
    """

    queryset = Exam.objects.all()
    serializer_class = ExamSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = super().get_queryset()

        # ✅ Session ↔ Exam FK 기반 필터
        session_id = self.request.query_params.get("session_id")
        if session_id:
            qs = qs.filter(sessions__id=int(session_id))

        # ✅ 확장: Lecture 기준 Exam 조회
        lecture_id = self.request.query_params.get("lecture_id")
        if lecture_id:
            qs = qs.filter(sessions__lecture_id=int(lecture_id))

        return qs.distinct().order_by("-created_at")
