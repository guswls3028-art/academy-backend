# apps/domains/assets/omr/urls.py
from django.urls import path

from apps.domains.assets.omr.views.omr_pdf_views import OMRPdfView
from apps.domains.assets.omr.views.omr_list_views import ObjectiveOMRTemplateListView

urlpatterns = [
    # 저장된 OMR PDF 조회
    path("pdf/<int:asset_id>/", OMRPdfView.as_view(), name="omr-pdf"),

    # 저장된 OMR 템플릿 리스트 조회
    path("templates/", ObjectiveOMRTemplateListView.as_view(), name="omr-template-list"),
]
