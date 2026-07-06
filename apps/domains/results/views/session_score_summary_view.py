# PATH: apps/domains/results/views/session_score_summary_view.py

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from apps.domains.results.permissions import IsTeacherOrAdmin
from apps.domains.results.services.session_score_summary_service import (
    SessionScoreSummaryService,
)
from apps.domains.results.serializers.session_score_summary import (
    SessionScoreSummarySerializer,
)
from apps.support.results.progress_read_dependencies import get_session_for_tenant_or_404


class SessionScoreSummaryView(APIView):
    """
    GET /results/admin/sessions/<session_id>/score-summary/

    ✅ results 도메인 기준
    - Session 단위 성적 통계
    - 운영/대시보드/AI 추천 입력용
    """

    permission_classes = [IsAuthenticated, IsTeacherOrAdmin]

    def get(self, request, session_id: int):
        # ✅ tenant isolation: verify session belongs to tenant
        get_session_for_tenant_or_404(session_id=int(session_id), tenant=request.tenant)

        data = SessionScoreSummaryService.build(
            session_id=int(session_id)
        )
        return Response(SessionScoreSummarySerializer(data).data)
