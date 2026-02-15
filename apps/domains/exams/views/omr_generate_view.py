# PATH: apps/domains/exams/views/omr_generate_view.py
from __future__ import annotations

import io
import uuid

from django.shortcuts import get_object_or_404

from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status

from apps.domains.exams.models import Exam, ExamAsset
from apps.domains.exams.serializers.exam_asset import ExamAssetSerializer
from apps.domains.exams.services.template_builder_service import TemplateBuilderService
from apps.domains.assets.omr.renderer.v245_final import render_to_bytes as render_omr_v245_pdf
from apps.domains.exams.services.template_resolver import assert_template_editable
from apps.core.r2_paths import ai_exam_asset_key
from apps.infrastructure.storage.r2 import upload_fileobj_to_r2
from apps.domains.results.permissions import IsTeacherOrAdmin


class GenerateOMRSheetAssetView(APIView):
    """
    ✅ PHASE 2-B
    POST /api/v1/exams/<template_exam_id>/generate-omr/

    - template exam에 대해서만 생성
    - derived regular 존재 시(봉인) 생성 금지(운영 사고 방지)
    - 생성 결과를 ExamAsset(omr_sheet)로 저장
    """

    permission_classes = [IsAuthenticated, IsTeacherOrAdmin]

    def post(self, request, exam_id: int):
        template_exam = get_object_or_404(Exam, id=int(exam_id), exam_type=Exam.ExamType.TEMPLATE)

        # 봉인: 이미 regular로 사용 중이면 구조/자산 변경 금지
        assert_template_editable(template_exam)

        init = TemplateBuilderService.ensure_initialized(template_exam)
        sheet = getattr(template_exam, "sheet", None)
        question_count = int(getattr(sheet, "total_questions", 0) or 0)

        # question_count가 0이면 최소한 template_exam의 OMR preset으로 생성하도록 fallback
        # (원하면 프론트에서 total_questions 먼저 확정시키는 흐름이 정석)
        if question_count <= 0:
            question_count = int(request.data.get("question_count") or 20)

        if question_count not in (10, 20, 30, 45):
            return Response({"detail": "question_count must be 10|20|30|45"}, status=status.HTTP_400_BAD_REQUEST)

        # 1) PDF 생성 — v245 OMR 시험지 (좌: 로고/시험명/과목/성명/전화/OMR 8자리, 우: 답란 1~15/16~30/31~45)
        pdf_bytes = render_omr_v245_pdf(question_count=question_count, debug_grid=False)
        bio = io.BytesIO(pdf_bytes)

        # 2) 업로드 (경로 통일: tenants/{id}/ai/exams/...)
        tenant_id = getattr(request, "tenant", None) and request.tenant.id
        if not tenant_id:
            return Response({"detail": "tenant required for upload"}, status=status.HTTP_400_BAD_REQUEST)
        key = ai_exam_asset_key(
            tenant_id=tenant_id,
            exam_id=template_exam.id,
            asset_type="omr_sheet",
            unique_id=uuid.uuid4().hex,
            ext="pdf",
        )
        upload_fileobj_to_r2(
            fileobj=bio,
            key=key,
            content_type="application/pdf",
        )

        # 3) ExamAsset 저장/갱신(최신 교체)
        obj, _ = ExamAsset.objects.update_or_create(
            exam=template_exam,
            asset_type=ExamAsset.AssetType.OMR_SHEET,
            defaults={
                "file_key": key,
                "file_type": "application/pdf",
                "file_size": len(pdf_bytes),
            },
        )

        return Response(ExamAssetSerializer(obj).data, status=status.HTTP_201_CREATED)
