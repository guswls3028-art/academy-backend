# apps/domains/assets/omr/views/omr_document_views.py
"""
OMR Document API Views — SSOT 기반 OMR 생성

⚠️ 테넌트 격리: 모든 뷰에서 request.tenant를 사용하여 로고/시험 resolve.
시험 조회 시 tenant-filtered queryset 사용.
"""
from __future__ import annotations

import re

from django.http import HttpResponse

from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from rest_framework import serializers

from apps.core.permissions import TenantResolvedAndStaff
from apps.domains.assets.omr.dto.omr_document import MAX_ESSAY_QUESTIONS, OMRDocument
from apps.domains.assets.omr.services.meta_generator import MAX_MC_QUESTIONS
from apps.domains.assets.omr.services.omr_document_service import OMRDocumentService
from apps.domains.assets.omr.renderer.html_renderer import OMRHtmlRenderer
from apps.domains.assets.omr.renderer.pdf_renderer import OMRPdfRenderer
from apps.support.omr.view_dependencies import get_exam_for_omr_document


def _get_exam(tenant, exam_id: int):
    """테넌트 격리된 시험 조회."""
    return get_exam_for_omr_document(tenant=tenant, exam_id=exam_id)


def _parse_omr_params(data: dict) -> dict:
    """요청 데이터에서 OMR 파라미터 추출 + 유효성 검사."""
    params = {}
    if "exam_title" in data and data["exam_title"]:
        params["exam_title"] = str(data["exam_title"])[:100]
    if "lecture_name" in data:
        params["lecture_name"] = str(data.get("lecture_name", ""))[:100]
    if "session_name" in data:
        params["session_name"] = str(data.get("session_name", ""))[:100]
    if "mc_count" in data:
        params["mc_count"] = serializers.IntegerField(
            min_value=0,
            max_value=MAX_MC_QUESTIONS,
        ).run_validation(data["mc_count"])
    if "essay_count" in data:
        params["essay_count"] = serializers.IntegerField(
            min_value=0,
            max_value=MAX_ESSAY_QUESTIONS,
        ).run_validation(data["essay_count"])
    if "n_choices" in data:
        params["n_choices"] = serializers.ChoiceField(
            choices=[5],
        ).run_validation(data["n_choices"])
    if "include_optional_essay_area" in data:
        params["include_optional_essay_area"] = serializers.BooleanField().run_validation(
            data["include_optional_essay_area"]
        )
    return params


def _validate_doc(doc: OMRDocument):
    """OMRDocument 유효성 검사. 오류 시 400 Response 반환, 정상이면 None."""
    errors = doc.validate()
    if errors:
        return Response({"detail": errors}, status=status.HTTP_400_BAD_REQUEST)
    return None


def _safe_filename(title: str) -> str:
    """파일명 안전 문자열."""
    safe = re.sub(r'[^\w가-힣 _-]', '', title).strip()
    return safe or "OMR"


def _render_preview_response(doc: OMRDocument) -> HttpResponse:
    """OMRDocument → HTML preview response. 모든 생성 경로의 SSOT."""
    html = OMRHtmlRenderer().render(doc)
    return HttpResponse(html, content_type="text/html; charset=utf-8")


def _render_pdf_response(doc: OMRDocument, *, tenant) -> HttpResponse:
    """OMRDocument → PDF download response. 모든 생성 경로의 SSOT."""
    doc = OMRDocumentService.fetch_logo_bytes(doc, tenant=tenant)
    pdf_bytes = OMRPdfRenderer().render(doc)

    filename = _safe_filename(doc.exam_title)
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{filename}_OMR.pdf"'
    return response


def _build_exam_doc(*, request, exam_id: int) -> tuple[OMRDocument | None, Response | None]:
    """시험 기반 OMRDocument 생성. validation error는 DRF Response로 반환."""
    exam = _get_exam(request.tenant, exam_id)
    params = _parse_omr_params(request.data)
    doc = OMRDocumentService.from_exam(
        exam=exam, tenant=request.tenant, **params
    )
    err = _validate_doc(doc)
    return (None, err) if err else (doc, None)


def _build_tools_doc(*, request) -> tuple[OMRDocument | None, Response | None]:
    """도구 기반 OMRDocument 생성. validation error는 DRF Response로 반환."""
    data = request.data
    exam_title = str(data.get("exam_title", "시험 답안지"))[:100]
    params = _parse_omr_params(data)
    params.pop("exam_title", None)

    doc = OMRDocumentService.from_params(
        tenant=request.tenant,
        exam_title=exam_title,
        **params,
    )
    err = _validate_doc(doc)
    return (None, err) if err else (doc, None)


# ──────────────────────────────────────────────
# Exam-bound endpoints
# ──────────────────────────────────────────────

class ExamOMRDefaultsView(APIView):
    """
    GET /api/v1/exams/{exam_id}/omr/defaults/

    시험 기반 OMR 기본값 반환 (시험명, 강의명, 차시명, 문항수, 로고 URL).
    """
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def get(self, request, exam_id: int):
        exam = _get_exam(request.tenant, exam_id)
        doc = OMRDocumentService.from_exam(exam=exam, tenant=request.tenant)
        return Response(doc.to_defaults_dict(), status=status.HTTP_200_OK)


class ExamOMRPreviewView(APIView):
    """
    POST /api/v1/exams/{exam_id}/omr/preview/

    시험 기반 OMR HTML 프리뷰 반환.
    """
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def post(self, request, exam_id: int):
        doc, err = _build_exam_doc(request=request, exam_id=exam_id)
        if err:
            return err
        return _render_preview_response(doc)


class ExamOMRPdfView(APIView):
    """
    POST /api/v1/exams/{exam_id}/omr/pdf/

    시험 기반 OMR PDF 다운로드 (실제 application/pdf).
    """
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def post(self, request, exam_id: int):
        doc, err = _build_exam_doc(request=request, exam_id=exam_id)
        if err:
            return err
        return _render_pdf_response(doc, tenant=request.tenant)


# ──────────────────────────────────────────────
# Standalone (Tools) endpoints
# ──────────────────────────────────────────────

class ToolsOMRPreviewView(APIView):
    """
    POST /api/v1/tools/omr/preview/

    도구 페이지용 OMR HTML 프리뷰 (시험 없이 직접 파라미터).
    """
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def post(self, request):
        doc, err = _build_tools_doc(request=request)
        if err:
            return err
        return _render_preview_response(doc)


class ToolsOMRPdfView(APIView):
    """
    POST /api/v1/tools/omr/pdf/

    도구 페이지용 OMR PDF 다운로드.
    """
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def post(self, request):
        doc, err = _build_tools_doc(request=request)
        if err:
            return err
        return _render_pdf_response(doc, tenant=request.tenant)
