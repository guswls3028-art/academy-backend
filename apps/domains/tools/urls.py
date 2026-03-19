# PATH: apps/domains/tools/urls.py
# 도구 API — PPT 생성, OMR 답안지 등 선생님 편의 도구

from django.urls import path
from .ppt.views import PptGenerateView
from apps.domains.assets.omr.views.omr_document_views import (
    ToolsOMRPreviewView,
    ToolsOMRPdfView,
)

urlpatterns = [
    path("ppt/generate/", PptGenerateView.as_view(), name="tools-ppt-generate"),
    path("omr/preview/", ToolsOMRPreviewView.as_view(), name="tools-omr-preview"),
    path("omr/pdf/", ToolsOMRPdfView.as_view(), name="tools-omr-pdf"),
]
