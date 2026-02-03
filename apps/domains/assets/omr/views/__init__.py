# apps/domains/assets/omr/views/__init__.py
from .omr_pdf_views import OMRPdfView
from .omr_list_views import ObjectiveOMRTemplateListView

__all__ = [
    "OMRPdfView",
    "ObjectiveOMRTemplateListView",
]
