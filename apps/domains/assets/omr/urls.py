# apps/domains/assets/omr/urls.py
from django.urls import path
from apps.domains.assets.omr.views.omr_pdf_views import ObjectiveOMRPdfView, ObjectiveOMRMetaView

urlpatterns = [
    path("objective/pdf/", ObjectiveOMRPdfView.as_view(), name="assets-omr-objective-pdf"),
    path("objective/meta/", ObjectiveOMRMetaView.as_view(), name="assets-omr-objective-meta"),
]
