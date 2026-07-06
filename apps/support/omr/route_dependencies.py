"""Route-level OMR view dependencies for non-assets domains."""

from __future__ import annotations

from apps.domains.assets.omr.views.omr_document_views import (
    ExamOMRDefaultsView,
    ExamOMRPreviewView,
    ExamOMRPdfView,
    ToolsOMRPreviewView,
    ToolsOMRPdfView,
)


__all__ = [
    "ExamOMRDefaultsView",
    "ExamOMRPreviewView",
    "ExamOMRPdfView",
    "ToolsOMRPreviewView",
    "ToolsOMRPdfView",
]

