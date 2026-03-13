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

from django.shortcuts import get_object_or_404
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from apps.core.permissions import TenantResolvedAndStaff
from apps.domains.lectures.models import Session
from apps.domains.exams.models import Exam
from apps.domains.homework_results.models import Homework


class SessionReorderView(APIView):
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def post(self, request, session_id: int):
        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response({"detail": "Tenant required"}, status=403)

        session = get_object_or_404(
            Session, id=int(session_id), lecture__tenant=tenant
        )

        exam_order = request.data.get("exams", [])
        hw_order = request.data.get("homeworks", [])

        if exam_order:
            exams = list(
                Exam.objects.filter(
                    id__in=[int(x) for x in exam_order],
                    sessions=session,
                )
            )
            exam_map = {int(e.id): e for e in exams}
            for idx, eid in enumerate(exam_order):
                exam = exam_map.get(int(eid))
                if exam and exam.display_order != idx:
                    exam.display_order = idx
                    exam.save(update_fields=["display_order"])

        if hw_order:
            homeworks = list(
                Homework.objects.filter(
                    id__in=[int(x) for x in hw_order],
                    session=session,
                )
            )
            hw_map = {int(h.id): h for h in homeworks}
            for idx, hid in enumerate(hw_order):
                hw = hw_map.get(int(hid))
                if hw and hw.display_order != idx:
                    hw.display_order = idx
                    hw.save(update_fields=["display_order"])

        return Response({"ok": True})
