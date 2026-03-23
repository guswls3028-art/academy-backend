# apps/domains/assets/omr/views/omr_document_views.py
"""
OMR Document API Views — SSOT 기반 OMR 생성

⚠️ 테넌트 격리: 모든 뷰에서 request.tenant를 사용하여 로고/시험 resolve.
시험 조회 시 tenant-filtered queryset 사용.
"""
from __future__ import annotations

import re

from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404

from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status

from apps.core.permissions import TenantResolvedAndStaff
from apps.domains.exams.models import Exam
from apps.domains.assets.omr.dto.omr_document import OMRDocument
from apps.domains.assets.omr.services.omr_document_service import OMRDocumentService
from apps.domains.assets.omr.renderer.html_renderer import OMRHtmlRenderer
from apps.domains.assets.omr.renderer.pdf_renderer import OMRPdfRenderer


def _get_exam(tenant, exam_id: int) -> Exam:
    """테넌트 격리된 시험 조회."""
    return get_object_or_404(
        Exam.objects.filter(
            Q(sessions__lecture__tenant=tenant)
            | Q(derived_exams__sessions__lecture__tenant=tenant)
        ).distinct(),
        id=int(exam_id),
    )


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
        params["mc_count"] = max(0, min(45, int(data["mc_count"] or 0)))
    if "essay_count" in data:
        params["essay_count"] = max(0, min(10, int(data["essay_count"] or 0)))
    if "n_choices" in data:
        params["n_choices"] = 5  # 5지선다 고정
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
        exam = _get_exam(request.tenant, exam_id)
        params = _parse_omr_params(request.data)
        doc = OMRDocumentService.from_exam(
            exam=exam, tenant=request.tenant, **params
        )
        err = _validate_doc(doc)
        if err:
            return err

        html = OMRHtmlRenderer().render(doc)
        return HttpResponse(html, content_type="text/html; charset=utf-8")


class ExamOMRPdfView(APIView):
    """
    POST /api/v1/exams/{exam_id}/omr/pdf/

    시험 기반 OMR PDF 다운로드 (실제 application/pdf).
    """
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def post(self, request, exam_id: int):
        exam = _get_exam(request.tenant, exam_id)
        params = _parse_omr_params(request.data)
        doc = OMRDocumentService.from_exam(
            exam=exam, tenant=request.tenant, **params
        )
        err = _validate_doc(doc)
        if err:
            return err

        doc = OMRDocumentService.fetch_logo_bytes(doc, tenant=request.tenant)
        pdf_bytes = OMRPdfRenderer().render(doc)

        filename = _safe_filename(doc.exam_title)
        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{filename}_OMR.pdf"'
        return response


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
        if err:
            return err

        html = OMRHtmlRenderer().render(doc)
        return HttpResponse(html, content_type="text/html; charset=utf-8")


class ToolsOMRPdfView(APIView):
    """
    POST /api/v1/tools/omr/pdf/

    도구 페이지용 OMR PDF 다운로드.
    """
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def post(self, request):
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
        if err:
            return err

        doc = OMRDocumentService.fetch_logo_bytes(doc, tenant=request.tenant)
        pdf_bytes = OMRPdfRenderer().render(doc)

        filename = _safe_filename(doc.exam_title)
        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{filename}_OMR.pdf"'
        return response
