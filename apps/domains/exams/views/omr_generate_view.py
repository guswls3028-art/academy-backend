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

from django.shortcuts import get_object_or_404

from rest_framework import serializers
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status

from apps.domains.exams.models import Exam
from apps.core.permissions import TenantResolvedAndStaff
from apps.domains.assets.omr.dto.omr_document import MAX_ESSAY_QUESTIONS
from apps.support.exams.view_dependencies import MAX_MC_QUESTIONS, build_omr_meta


class LegacyOMRParamsSerializer(serializers.Serializer):
    mc_count = serializers.IntegerField(
        min_value=0,
        max_value=MAX_MC_QUESTIONS,
        required=False,
    )
    essay_count = serializers.IntegerField(
        min_value=0,
        max_value=MAX_ESSAY_QUESTIONS,
        required=False,
    )
    n_choices = serializers.ChoiceField(choices=[5], required=False)


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
            Exam.objects.filter(tenant=tenant),
            id=int(exam_id),
        )

        sheet = getattr(exam, "sheet", None)
        total_questions = int(getattr(sheet, "total_questions", 0) or 0)

        if request.data.get("mc_count") in (None, "") and sheet:
            from apps.support.omr.contract_builder import build_omr_sheet_contract

            contract = build_omr_sheet_contract(sheet=sheet, exam=exam)
            default_mc = contract.choice_count
            default_essay = contract.essay_count
        else:
            default_mc = total_questions
            default_essay = 0

        raw_params = {
            key: value
            for key, value in request.data.items()
            if key in {"mc_count", "essay_count", "n_choices"}
            and value not in (None, "")
        }
        params = LegacyOMRParamsSerializer(data=raw_params)
        params.is_valid(raise_exception=True)
        mc_count = params.validated_data.get("mc_count", default_mc)
        essay_count = params.validated_data.get("essay_count", default_essay)
        n_choices = params.validated_data.get("n_choices", 5)

        if mc_count <= 0 and essay_count <= 0:
            mc_count = total_questions or 20

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
