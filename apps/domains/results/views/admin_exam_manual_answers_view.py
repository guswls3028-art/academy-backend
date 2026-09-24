from __future__ import annotations

from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.domains.results.permissions import IsTeacherOrAdmin
from apps.domains.results.services.manual_exam_answers import (
    apply_manual_answers,
    get_manual_answers,
    preview_manual_answers,
)
from apps.support.results.admin_exam_dependencies import get_regular_active_exam_for_tenant


class AdminExamManualAnswersView(APIView):
    """Preview or atomically confirm one student's offline objective answers."""

    permission_classes = [IsAuthenticated, IsTeacherOrAdmin]

    def get(self, request, exam_id: int, enrollment_id: int):
        exam = get_regular_active_exam_for_tenant(
            exam_id=int(exam_id), tenant=request.tenant,
        )
        return Response(get_manual_answers(
            exam=exam, tenant=request.tenant, enrollment_id=int(enrollment_id),
        ))

    def post(self, request, exam_id: int, enrollment_id: int):
        exam = get_regular_active_exam_for_tenant(
            exam_id=int(exam_id), tenant=request.tenant,
        )
        if request.data.get("apply") is True:
            return Response(apply_manual_answers(
                exam=exam, tenant=request.tenant,
                enrollment_id=int(enrollment_id), payload=request.data,
                user_id=int(request.user.id),
            ))
        preview, _ = preview_manual_answers(
            exam=exam, tenant=request.tenant,
            enrollment_id=int(enrollment_id), payload=request.data,
        )
        return Response({**preview, "applied": False})
