# apps/domains/student_app/sessions/views.py
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from apps.domains.student_app.permissions import IsStudentOrParent
from .serializers import StudentSessionSerializer


class StudentSessionListView(APIView):
    """
    GET /sessions/me/
    """

    permission_classes = [IsAuthenticated, IsStudentOrParent]

    def get(self, request):
        return Response(StudentSessionSerializer([], many=True).data)


class StudentSessionDetailView(APIView):
    """
    GET /sessions/{id}/
    """

    permission_classes = [IsAuthenticated, IsStudentOrParent]

    def get(self, request, pk):
        data = {
            "id": pk,
            "title": f"Session {pk}",
            "date": None,
            "status": None,
            "exam_ids": [],
        }
        return Response(StudentSessionSerializer(data).data)
