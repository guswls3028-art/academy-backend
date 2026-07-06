# PATH: apps/domains/results/views/session_reorder_view.py
"""
POST /api/v1/results/admin/sessions/<session_id>/reorder/

성적탭에서 시험/과제 표시 순서를 변경합니다.

Payload:
{
  "exams": [3, 1, 2],       // exam_id 순서
  "homeworks": [5, 4, 6]    // homework_id 순서
}
"""
from __future__ import annotations

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from apps.core.permissions import TenantResolvedAndStaff
from apps.support.results.session_reorder_dependencies import (
    get_session_for_tenant,
    reorder_session_exams,
    reorder_session_homeworks,
)


class SessionReorderView(APIView):
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def post(self, request, session_id: int):
        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response({"detail": "Tenant required"}, status=403)

        session = get_session_for_tenant(session_id=int(session_id), tenant=tenant)

        exam_order = request.data.get("exams", [])
        hw_order = request.data.get("homeworks", [])

        if exam_order:
            reorder_session_exams(
                session=session,
                ordered_ids=[int(exam_id) for exam_id in exam_order],
            )

        if hw_order:
            reorder_session_homeworks(
                session=session,
                ordered_ids=[int(homework_id) for homework_id in hw_order],
            )

        return Response({"ok": True})
