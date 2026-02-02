# PATH: apps/domains/assets/omr/urls.py
from django.urls import path

from apps.domains.assets.omr.views.omr_pdf_views import (
    ObjectiveOMRPdfView,
    ObjectiveOMRMetaView,
)
from apps.domains.assets.omr.views.omr_save_views import ObjectiveOMRSaveView
from apps.domains.assets.omr.views.omr_list_views import ObjectiveOMRTemplateListView

urlpatterns = [
    path("objective/pdf/", ObjectiveOMRPdfView.as_view(), name="assets-omr-objective-pdf"),
    path("objective/meta/", ObjectiveOMRMetaView.as_view(), name="assets-omr-objective-meta"),
    path("objective/save/", ObjectiveOMRSaveView.as_view(), name="assets-omr-objective-save"),
    path(
        "objective/templates/",
        ObjectiveOMRTemplateListView.as_view(),
        name="assets-omr-objective-template-list",
    ),
]
