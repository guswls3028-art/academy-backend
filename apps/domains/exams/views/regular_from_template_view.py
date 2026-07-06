# PATH: apps/domains/exams/views/regular_from_template_view.py
from __future__ import annotations

from django.shortcuts import get_object_or_404

from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status

from apps.core.permissions import TenantResolvedAndMember
from apps.domains.exams.models import Exam
from apps.domains.exams.serializers.exam import ExamSerializer
from apps.domains.exams.services.regular_exam_factory import RegularExamFactory
from apps.support.exams.view_dependencies import IsTeacherOrAdmin, get_session_for_tenant_or_404


class RegularExamFromTemplateView(APIView):
    """
    ✅ PHASE 2-A
    POST /api/v1/exams/<template_exam_id>/spawn-regular/

    목적:
    - 기존 ExamViewSet.create(regular)도 그대로 유지
    - 템플릿 화면에서 "이 템플릿으로 실제 시험 만들기"를 더 단순하게 제공
    """

    permission_classes = [IsAuthenticated, TenantResolvedAndMember, IsTeacherOrAdmin]

    def post(self, request, exam_id: int):
        tenant = request.tenant
        template_exam = get_object_or_404(
            Exam.objects.filter(tenant=tenant),
            id=int(exam_id),
            exam_type=Exam.ExamType.TEMPLATE,
        )

        session_id = request.data.get("session_id")
        title = request.data.get("title")  # optional
        description = request.data.get("description")  # optional

        if not session_id:
            return Response({"detail": "session_id required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            session_id = int(session_id)
        except (TypeError, ValueError):
            return Response({"detail": "session_id must be integer"}, status=status.HTTP_400_BAD_REQUEST)

        # ✅ Tenant isolation: validate session belongs to same tenant
        get_session_for_tenant_or_404(session_id=session_id, tenant=tenant)

        factory = RegularExamFactory()
        regular = factory.create_regular_from_template(
            template_exam=template_exam,
            session_id=session_id,
            title=str(title).strip() if title else None,
            description=str(description).strip() if description else None,
            tenant=tenant,
        )

        return Response(ExamSerializer(regular).data, status=status.HTTP_201_CREATED)
