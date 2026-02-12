# apps/domains/student_app/exams/views.py
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from apps.domains.student_app.permissions import IsStudentOrParent
from .serializers import StudentExamSerializer


class StudentExamListView(APIView):
    """
    GET /exams/
    """

    permission_classes = [IsAuthenticated, IsStudentOrParent]

    def get(self, request):
        return Response({"items": []})


class StudentExamDetailView(APIView):
    """
    GET /exams/{id}/
    """

    permission_classes = [IsAuthenticated, IsStudentOrParent]

    def get(self, request, pk):
        data = {
            "id": pk,
            "title": f"Exam {pk}",
            "open_at": None,
            "close_at": None,
            "allow_retake": True,
            "max_attempts": 1,
            "pass_score": 60,
            "description": None,
            "session_id": None,
        }
        return Response(StudentExamSerializer(data).data)
