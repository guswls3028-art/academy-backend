# apps/domains/assets/omr/views/__init__.py
from .omr_pdf_views import ObjectiveOMRPdfView, ObjectiveOMRMetaView
from .omr_save_views import ObjectiveOMRSaveView
from .omr_list_views import ObjectiveOMRTemplateListView

__all__ = [
    "ObjectiveOMRPdfView",
    "ObjectiveOMRMetaView",
    "ObjectiveOMRSaveView",
    "ObjectiveOMRTemplateListView",
]