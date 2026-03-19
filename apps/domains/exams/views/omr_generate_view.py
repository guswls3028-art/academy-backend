# PATH: apps/domains/exams/views/omr_generate_view.py
"""
⚠️ DEPRECATED — 이 뷰는 레거시입니다.
새 OMR 시스템은 omr_document_views.py를 사용합니다:
  - GET /exams/{id}/omr/defaults/
  - POST /exams/{id}/omr/preview/
  - POST /exams/{id}/omr/pdf/

기존 호출자 호환을 위해 유지합니다.
"""
from __future__ import annotations

from django.db.models import Q
from django.shortcuts import get_object_or_404

from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status

from apps.domains.exams.models import Exam
from apps.domains.assets.omr.services.meta_generator import build_omr_meta
from apps.core.permissions import TenantResolvedAndStaff


class GenerateOMRSheetAssetView(APIView):
    """
    POST /api/v1/exams/<exam_id>/generate-omr/

    시험의 문항 구성에 맞는 OMR 메타(좌표)를 반환한다.
    프론트엔드에서 /omr-sheet.html?mc=N&essay=M 으로 답안지를 생성/인쇄한다.
    """
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def post(self, request, exam_id: int):
        tenant = request.tenant

        exam = get_object_or_404(
            Exam.objects.filter(
                Q(sessions__lecture__tenant=tenant)
                | Q(derived_exams__sessions__lecture__tenant=tenant)
            ).distinct(),
            id=int(exam_id),
        )

        sheet = getattr(exam, "sheet", None)
        total_questions = int(getattr(sheet, "total_questions", 0) or 0)

        mc_count = int(request.data.get("mc_count", 0) or total_questions)
        essay_count = int(request.data.get("essay_count", 0) or 0)
        n_choices = int(request.data.get("n_choices", 5) or 5)

        if mc_count <= 0:
            mc_count = total_questions or 20

        if mc_count > 45:
            return Response(
                {"detail": "객관식은 최대 45문항입니다."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        meta = build_omr_meta(
            question_count=mc_count,
            n_choices=n_choices,
            essay_count=essay_count,
        )

        # OMR 시트 URL 구성
        omr_url = f"/omr-sheet.html?exam={exam.title}&mc={mc_count}&essay={essay_count}&choices={n_choices}"

        return Response({
            "omr_url": omr_url,
            "meta": meta,
            "mc_count": mc_count,
            "essay_count": essay_count,
            "n_choices": n_choices,
        }, status=status.HTTP_200_OK)
