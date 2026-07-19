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

        parsed_ints = {}
        for key, minimum, maximum, default in (
            ("student_id", 1, None, None),
            ("grade", 1, 20, None),
            ("page", 1, None, 1),
            ("page_size", 1, 100, 30),
            ("review_page", 1, None, 1),
            ("review_page_size", 1, 50, 20),
        ):
            raw = request.query_params.get(key)
            if raw in (None, ""):
                parsed_ints[key] = default
                continue
            try:
                value = int(raw)
            except (TypeError, ValueError):
                return Response({"detail": f"{key} must be integer"}, status=400)
            if value < minimum or (maximum is not None and value > maximum):
                return Response({"detail": f"{key} out of range"}, status=400)
            parsed_ints[key] = value

        source = str(request.query_params.get("source") or "overall").strip()
        score_band = str(request.query_params.get("score_band") or "all").strip()
        trend = str(request.query_params.get("trend") or "all").strip()
        sort = str(request.query_params.get("sort") or "attention").strip()
        allowed_values = {
            "source": ({"overall", "academy", "school", "mock"}, source),
            "score_band": ({"all", "under_60", "60_to_79", "80_plus", "unscored"}, score_band),
            "trend": ({"all", "up", "down", "flat", "insufficient"}, trend),
            "sort": ({"attention", "latest_desc", "change_desc", "name"}, sort),
        }
        for key, (choices, value) in allowed_values.items():
            if value not in choices:
                return Response({"detail": f"{key} invalid"}, status=400)
        search = str(request.query_params.get("search") or "").strip()[:80]
        subject = str(request.query_params.get("subject") or "").strip()[:50]

        days = normalize_performance_days(request.query_params.get("days"), default=180)
        return Response(
            build_student_performance_console(
                tenant=tenant,
                days=days,
                lecture_id=lecture_id,
                student_id=parsed_ints["student_id"],
                search=search,
                grade=parsed_ints["grade"],
                source=source,
                subject=subject,
                score_band=score_band,
                trend=trend,
                sort=sort,
                page=parsed_ints["page"],
                page_size=parsed_ints["page_size"],
                review_page=parsed_ints["review_page"],
                review_page_size=parsed_ints["review_page_size"],
            )
        )
