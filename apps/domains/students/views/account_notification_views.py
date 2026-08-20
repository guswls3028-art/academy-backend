# PATH: apps/domains/students/views/account_notification_views.py

from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.api.common.query_params import parse_query_int
from apps.core.permissions import TenantResolvedAndStaff
from apps.domains.students.models import Student
from apps.domains.students.services.account_recovery import list_recent_account_notification_logs


class StudentAccountNotificationLogView(APIView):
    """GET: 학생 상세용 최근 계정 알림톡 발송 상태."""

    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def get(self, request, student_id: int):
        student = Student.objects.filter(
            tenant=request.tenant,
            pk=student_id,
            deleted_at__isnull=True,
        ).first()
        if not student:
            return Response({"detail": "학생 정보를 찾을 수 없습니다."}, status=404)

        limit = parse_query_int(
            request.query_params,
            "limit",
            default=5,
            min_value=1,
        )

        return Response({
            "results": list_recent_account_notification_logs(student, limit=limit),
        })
