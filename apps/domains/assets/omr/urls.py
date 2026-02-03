from django.urls import path
from apps.domains.assets.omr.views import omr_pdf_views, omr_save_views, omr_list_views

urlpatterns = [
    # PDF 생성 및 미리보기
    path("objective/pdf/", omr_pdf_views.ObjectiveOMRPdfView.as_view(), name="omr-pdf"),
    # 좌표 메타데이터 조회
    path("objective/meta/", omr_pdf_views.ObjectiveOMRMetaView.as_view(), name="omr-meta"),
    # 시험지 저장
    path("objective/save/", omr_save_views.ObjectiveOMRSaveView.as_view(), name="omr-save"),
    # 저장된 목록 조회
    path("objective/templates/", omr_list_views.ObjectiveOMRTemplateListView.as_view(), name="omr-list"),
]