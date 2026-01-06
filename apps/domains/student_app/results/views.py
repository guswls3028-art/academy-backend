# apps/domains/student_app/results/views.py
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from apps.domains.student_app.permissions import IsStudent
from .serializers import (
    MyExamResultSerializer,
    MyExamResultItemSerializer,
)


class MyExamResultView(APIView):
    """
    GET /results/me/exams/{exam_id}/
    """

    permission_classes = [IsAuthenticated, IsStudent]

    def get(self, request, exam_id):
        data = {
            "exam_id": exam_id,
            "attempt_id": 1,
            "total_score": 0,
            "max_score": 100,
            "is_pass": False,
            "submitted_at": None,
            "can_retake": True,
        }
        return Response(MyExamResultSerializer(data).data)


class MyExamResultItemsView(APIView):
    """
    GET /results/me/exams/{exam_id}/items/
    """

    permission_classes = [IsAuthenticated, IsStudent]

    def get(self, request, exam_id):
        return Response({"items": []})
