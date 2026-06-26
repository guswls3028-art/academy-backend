# PATH: apps/domains/matchup/urls.py
from django.urls import path
from . import views, views_hit_report, views_proposal, views_public_cleanup

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
    # Phase A (2026-05-09) — page-level state (auto/skip/manual). basic_definition SSOT MVP 1단계.
    path("documents/<int:doc_id>/page-states/", views.DocumentPageStatesView.as_view()),
    path("documents/<int:doc_id>/page-states/<int:page_idx>/", views.DocumentPageStateSingleView.as_view()),
    path("documents/<int:doc_id>/reanalyze/", views.DocumentReanalyzeView.as_view()),
    path("documents/<int:doc_id>/manual-crop/", views.DocumentManualCropView.as_view()),
    path("documents/<int:doc_id>/public-cleanup/", views_public_cleanup.DocumentPublicCleanupView.as_view()),
    path("documents/<int:doc_id>/paste-problem/", views.DocumentPasteProblemView.as_view()),
    path("documents/<int:doc_id>/merge-problems/", views.DocumentMergeProblemsView.as_view()),
    path("documents/<int:doc_id>/bulk-delete-problems/", views.DocumentBulkDeleteProblemsView.as_view()),
    path("documents/<int:doc_id>/cross-matches/", views.DocumentCrossMatchesView.as_view()),
    path("documents/<int:doc_id>/job/", views.DocumentJobView.as_view()),
    path("documents/<int:doc_id>/retry/", views.DocumentRetryView.as_view()),
    path("documents/<int:doc_id>/hit-report.pdf", views_hit_report.DocumentHitReportPdfView.as_view()),
    path("documents/<int:doc_id>/hit-report-draft/", views_hit_report.HitReportDraftView.as_view()),

    # Curated hit reports — 강사 1인 매치업 적중 보고서 (수업 히스토리 + 학원 KPI + 신뢰자료)
    path("hit-reports/", views_hit_report.HitReportListView.as_view()),
    path("hit-reports/<int:report_id>/", views_hit_report.HitReportDetailView.as_view()),
    path("hit-reports/<int:report_id>/entries/", views_hit_report.HitReportEntriesUpsertView.as_view()),
    path("hit-reports/<int:report_id>/submit/", views_hit_report.HitReportSubmitView.as_view()),
    path("hit-reports/<int:report_id>/unsubmit/", views_hit_report.HitReportUnsubmitView.as_view()),
    path("hit-reports/<int:report_id>/curated.pdf", views_hit_report.HitReportPdfView.as_view()),
    # 카페·블로그 게시용 raw asset (PNG + summary.md). 강사가 본인 명의로 자유 게시.
    path("hit-reports/<int:report_id>/share.zip", views_hit_report.HitReportZipExportView.as_view()),
    # admin 포탈 widget — 학원 홈페이지에 게시된 보고서 mini list (적중보고서 탭 상단 띠).
    # 학원장 mental model 정합 (2026-05-11): admin 작성/관리 자리에서 게시 결과 즉시 확인.
    path("hit-reports/board-preview/", views_hit_report.HitReportBoardPreviewView.as_view()),
    # 공개 랜딩 페이지용 적중보고서 카드 메타 (인증 X, 테넌트 격리 절대)
    path("landing/public/", views_hit_report.HitReportLandingPublicView.as_view()),
    # 학원장 picker에 박은 보고서만 본문 PDF 공개 (외부 학부모/학생 신뢰 확보 동선)
    path("landing/public/<int:report_id>/curated.pdf", views_hit_report.HitReportLandingPublicPdfView.as_view()),

    # 1클릭 공유 토큰 (#67, 2026-05-12) — 선생→학생 카톡 링크 한 번 클릭 PDF.
    # 관리: 학원장/admin/author 만. public: token UUID 만으로 통과.
    path("hit-reports/<int:report_id>/share-link/", views_hit_report.HitReportShareLinkView.as_view()),
    path("share/<uuid:token>/", views_hit_report.HitReportShareMetaView.as_view()),
    path("share/<uuid:token>/curated.pdf", views_hit_report.HitReportSharePdfView.as_view()),

    # Categories
    path("categories/", views.CategoryListView.as_view()),
    path("categories/rename/", views.CategoryRenameView.as_view()),
    path("categories/assign/", views.CategoryAssignView.as_view()),

    # Problems
    path("problems/", views.ProblemListView.as_view()),
    path("problems/presign/", views.ProblemPresignView.as_view()),
    path("problems/<int:problem_id>/", views.ProblemDetailView.as_view()),
    path(
        "problems/<int:problem_id>/public-cleanup/approve/",
        views_public_cleanup.ProblemPublicCleanupApproveView.as_view(),
    ),
    path("problems/<int:problem_id>/public-image/", views_public_cleanup.ProblemPublicImageUploadView.as_view()),
    path("problems/<int:problem_id>/similar/", views.SimilarProblemView.as_view()),

    # Stage 3 Phase 3.4 — ProblemSegmentationProposal admin API.
    # 검수 큐 / approve / reject. Phase 3.3 helper 재사용. callback 미연결.
    path("proposals/", views_proposal.ProposalListView.as_view()),
    path("proposals/<int:proposal_id>/", views_proposal.ProposalDetailView.as_view()),
    path("proposals/<int:proposal_id>/approve/", views_proposal.ProposalApproveView.as_view()),
    path("proposals/<int:proposal_id>/reject/", views_proposal.ProposalRejectView.as_view()),
]
