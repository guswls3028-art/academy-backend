from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.domains.results.permissions import IsTeacherOrAdmin
from apps.support.results.student_performance_console import (
    build_student_performance_console,
    normalize_performance_days,
    performance_lecture_exists,
)


class AdminStudentPerformanceView(APIView):
    """GET /results/admin/student-performance/ — cumulative roster score summaries."""

    permission_classes = [IsAuthenticated, IsTeacherOrAdmin]

    def get(self, request):
        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response({"detail": "tenant not resolved"}, status=403)

        raw_lecture_id = request.query_params.get("lecture_id")
        lecture_id = None
        if raw_lecture_id not in (None, ""):
            try:
                lecture_id = int(raw_lecture_id)
            except (TypeError, ValueError):
                return Response({"detail": "lecture_id must be integer"}, status=400)
            if lecture_id <= 0:
                return Response({"detail": "lecture_id must be positive"}, status=400)
            if not performance_lecture_exists(tenant=tenant, lecture_id=lecture_id):
                return Response({"detail": "lecture not found"}, status=404)

        days = normalize_performance_days(request.query_params.get("days"), default=180)
        return Response(
            build_student_performance_console(
                tenant=tenant,
                days=days,
                lecture_id=lecture_id,
            )
        )
