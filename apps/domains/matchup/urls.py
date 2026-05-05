# PATH: apps/domains/matchup/urls.py
from django.urls import path
from . import views

urlpatterns = [
    # Documents
    path("documents/upload/", views.DocumentUploadView.as_view()),
    path("documents/promote/", views.DocumentPromoteFromInventoryView.as_view()),
    path("documents/", views.DocumentListView.as_view()),
    path("documents/<int:doc_id>/", views.DocumentDetailView.as_view()),
    path("documents/<int:doc_id>/preview/", views.DocumentPreviewView.as_view()),
    path("documents/<int:doc_id>/pages/", views.DocumentPagesView.as_view()),
    path("documents/<int:doc_id>/pages/<int:page_idx>/exclude/", views.DocumentPageExcludeView.as_view()),
    path("documents/<int:doc_id>/pages/<int:page_idx>/include/", views.DocumentPageIncludeView.as_view()),
    path("documents/<int:doc_id>/pages/<int:page_idx>/vlm-classify/", views.DocumentPageVlmClassifyView.as_view()),
    path("documents/<int:doc_id>/reanalyze/", views.DocumentReanalyzeView.as_view()),
    path("documents/<int:doc_id>/manual-crop/", views.DocumentManualCropView.as_view()),
    path("documents/<int:doc_id>/paste-problem/", views.DocumentPasteProblemView.as_view()),
    path("documents/<int:doc_id>/merge-problems/", views.DocumentMergeProblemsView.as_view()),
    path("documents/<int:doc_id>/bulk-delete-problems/", views.DocumentBulkDeleteProblemsView.as_view()),
    path("documents/<int:doc_id>/cross-matches/", views.DocumentCrossMatchesView.as_view()),
    path("documents/<int:doc_id>/job/", views.DocumentJobView.as_view()),
    path("documents/<int:doc_id>/retry/", views.DocumentRetryView.as_view()),
    path("documents/<int:doc_id>/hit-report.pdf", views.DocumentHitReportPdfView.as_view()),
    path("documents/<int:doc_id>/hit-report-draft/", views.HitReportDraftView.as_view()),

    # Curated hit reports — 강사 1인 매치업 적중 보고서 (수업 히스토리 + 학원 KPI + 신뢰자료)
    path("hit-reports/", views.HitReportListView.as_view()),
    path("hit-reports/<int:report_id>/", views.HitReportDetailView.as_view()),
    path("hit-reports/<int:report_id>/entries/", views.HitReportEntriesUpsertView.as_view()),
    path("hit-reports/<int:report_id>/submit/", views.HitReportSubmitView.as_view()),
    path("hit-reports/<int:report_id>/curated.pdf", views.HitReportPdfView.as_view()),
    # 카페·블로그 게시용 raw asset (PNG + summary.md). 강사가 본인 명의로 자유 게시.
    path("hit-reports/<int:report_id>/share.zip", views.HitReportZipExportView.as_view()),

    # Categories
    path("categories/", views.CategoryListView.as_view()),
    path("categories/rename/", views.CategoryRenameView.as_view()),
    path("categories/assign/", views.CategoryAssignView.as_view()),

    # Problems
    path("problems/", views.ProblemListView.as_view()),
    path("problems/presign/", views.ProblemPresignView.as_view()),
    path("problems/<int:problem_id>/", views.ProblemDetailView.as_view()),
    path("problems/<int:problem_id>/similar/", views.SimilarProblemView.as_view()),
]
