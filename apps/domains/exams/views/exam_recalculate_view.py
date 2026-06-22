from __future__ import annotations

from django.shortcuts import get_object_or_404

from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import TenantResolvedAndMember
from apps.domains.exams.models import Exam
from apps.domains.results.permissions import IsTeacherOrAdmin
from apps.support.submissions.dependencies import regrade_exam_submissions


class ExamRecalculateView(APIView):
    """
    POST /api/v1/exams/<exam_id>/recalculate/

    Re-grade completed/ready submissions after answer-key or score-setting changes.
    The frontend has exposed this action for admins/teachers, so the API must be
    explicit and tenant-scoped instead of falling through to 404.
    """

    permission_classes = [IsAuthenticated, TenantResolvedAndMember, IsTeacherOrAdmin]

    def post(self, request, exam_id: int):
        tenant = getattr(request, "tenant", None)
        exam = get_object_or_404(
            Exam.objects.filter(tenant=tenant),
            id=int(exam_id),
        )
        return Response(
            regrade_exam_submissions(
                tenant=tenant,
                exam_id=int(exam.id),
                actor="ExamRecalculateView",
            )
        )
