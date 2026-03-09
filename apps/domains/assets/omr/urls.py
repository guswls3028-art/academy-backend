# apps/domains/assets/omr/urls.py
from django.urls import path

from apps.domains.assets.omr.views.omr_pdf_views import OMRPdfView
from apps.domains.assets.omr.views.omr_list_views import (
    ObjectiveOMRTemplateListView,
    ObjectiveOMRMetaView,
)

urlpatterns = [
    path("pdf/<int:asset_id>/", OMRPdfView.as_view(), name="omr-pdf"),
    path("templates/", ObjectiveOMRTemplateListView.as_view(), name="omr-template-list"),
    path("objective/meta/", ObjectiveOMRMetaView.as_view(), name="assets-omr-objective-meta"),
]
