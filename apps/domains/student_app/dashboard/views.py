# apps/domains/student_app/dashboard/views.py
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from apps.domains.student_app.permissions import IsStudentOrParent
from .serializers import StudentDashboardSerializer


class StudentDashboardView(APIView):
    """
    GET /student/dashboard/
    """

    permission_classes = [IsAuthenticated, IsStudentOrParent]

    def get(self, request):
        data = {
            "notices": [],
            "today_sessions": [],
            "badges": {},
            "tenant_info": None,
        }
        tenant = getattr(request, "tenant", None)
        if tenant:
            data["tenant_info"] = {
                "name": (getattr(tenant, "name", None) or "").strip(),
                "phone": (getattr(tenant, "phone", None) or "").strip(),
                "headquarters_phone": (getattr(tenant, "headquarters_phone", None) or "").strip(),
            }
        return Response(StudentDashboardSerializer(data).data)
