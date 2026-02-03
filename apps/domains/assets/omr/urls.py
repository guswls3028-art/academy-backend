# apps/domains/assets/omr/urls.py
from django.urls import path
from apps.domains.assets.omr.views.omr_pdf_views import ObjectiveOMRPdfView, ObjectiveOMRMetaView
from apps.domains.assets.omr.views.omr_save_views import ObjectiveOMRSaveView
from apps.domains.assets.omr.views.omr_list_views import ObjectiveOMRTemplateListView

urlpatterns = [
    # 1. 생성/미리보기 (POST) 및 메타데이터(GET)
    path("objective/pdf/", ObjectiveOMRPdfView.as_view(), name="omr-pdf-generate"),
    path("objective/meta/", ObjectiveOMRMetaView.as_view(), name="omr-meta-info"),
    
    # 2. 시험지 저장 (POST)
    path("objective/save/", ObjectiveOMRSaveView.as_view(), name="omr-save"),
    
    # 3. 저장된 템플릿 리스트 조회 (GET) - 프론트엔드 호출 규격 준수
    path("objective/templates/", ObjectiveOMRTemplateListView.as_view(), name="omr-template-list"),
]