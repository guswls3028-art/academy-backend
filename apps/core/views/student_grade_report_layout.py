"""Manager-facing configuration for the student growth report."""

from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import TenantResolvedAndStaff
from apps.core.services.ops_audit import record_audit
from apps.core.services.student_grade_report_layout import (
    get_student_grade_report_layout,
    save_student_grade_report_layout,
)
from apps.core.services.tenant_access import get_authorized_tenant_role


class StudentGradeReportLayoutView(APIView):
    """GET/PATCH /api/v1/core/student-grade-report-layout/ for owner/admin."""

    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    @staticmethod
    def _can_manage(request) -> bool:
        return get_authorized_tenant_role(
            getattr(request, "user", None),
            getattr(request, "tenant", None),
        ) in {"owner", "admin"}

    def get(self, request):
        if not self._can_manage(request):
            return Response({"detail": "학원 관리자만 성적표 구성을 변경할 수 있습니다."}, status=403)
        return Response(get_student_grade_report_layout(tenant=request.tenant))

    def patch(self, request):
        if not self._can_manage(request):
            return Response({"detail": "학원 관리자만 성적표 구성을 변경할 수 있습니다."}, status=403)
        previous = get_student_grade_report_layout(tenant=request.tenant)
        layout = save_student_grade_report_layout(
            tenant=request.tenant,
            value=request.data,
        )
        record_audit(
            request,
            action="student_grade_report.layout.update",
            summary="학생 성장 그래프 구성을 변경함",
            target_tenant=request.tenant,
            payload={"before": previous, "after": layout},
        )
        return Response(layout)
