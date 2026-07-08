from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.domains.results.permissions import IsTeacherOrAdmin
from apps.domains.results.services.enterprise_analytics import (
    build_teacher_enterprise_analytics,
    normalize_analytics_days,
)


class AdminEnterpriseAnalyticsView(APIView):
    """
    GET /results/admin/analytics/
    Tenant-scoped operating analytics for scores, manual score entry, and auto grading.
    """

    permission_classes = [IsAuthenticated, IsTeacherOrAdmin]

    def get(self, request):
        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response({"detail": "tenant not resolved"}, status=403)
        days = normalize_analytics_days(request.query_params.get("days"), default=180)
        return Response(build_teacher_enterprise_analytics(tenant=tenant, days=days))
