"""
Teacher App BFF Views.
모바일 앱에 필요한 집계 데이터를 단일 응답으로 제공.
"""
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import TenantResolvedAndStaff
from apps.support.teacher_app.view_dependencies import notification_summary_counts


class NotificationSummaryView(APIView):
    """
    GET: 미처리 알림 건수 집계.
    - qna_pending: 답변 없는 Q&A
    - registration_pending: 대기 중 등록요청
    - clinic_pending: 대기 중 클리닉 예약
    """

    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def get(self, request):
        tenant = request.tenant

        counts = notification_summary_counts(tenant=tenant)
        qna_pending = counts["qna_pending"]
        counsel_pending = counts["counsel_pending"]
        registration_pending = counts["registration_pending"]
        clinic_pending = counts["clinic_pending"]

        total = qna_pending + counsel_pending + registration_pending + clinic_pending

        return Response({
            "total": total,
            "qna_pending": qna_pending,
            "counsel_pending": counsel_pending,
            "registration_pending": registration_pending,
            "clinic_pending": clinic_pending,
        })
