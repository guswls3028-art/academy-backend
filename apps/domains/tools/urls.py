# PATH: apps/domains/tools/urls.py
# 도구 API — PPT 생성, OMR 답안지, 타이머 다운로드 등 선생님 편의 도구

from django.urls import path
from .ppt.views import PptGenerateView
from .problem_studio.views import (
    ProblemStudioJobCreateView,
    ProblemStudioJobStatusView,
    ProblemStudioTransferDocumentView,
    ProblemStudioTransferJobCreateView,
    ProblemStudioTransferJobStatusView,
    ProblemStudioBetaAccessView,
    ProblemStudioExplanationRunCreateView,
    ProblemStudioExplanationRunStatusView,
    ProblemStudioExplanationRunResumeView,
    ProblemStudioHangulHandoffCreateView,
    ProblemStudioHangulHandoffConsumeView,
    ProblemStudioHangulCompanionDownloadView,
    ProblemStudioFontCollectionView,
    ProblemStudioFontDetailView,
    ProblemStudioDocumentStyleView,
    ProblemStudioVoiceProfileCollectionView,
    ProblemStudioVoiceProfileDetailView,
    ProblemStudioVoiceSampleCollectionView,
    ProblemStudioGenerationReviewView,
)
from .problem_solver.views import (
    TeacherProblemExplanationJobCreateView,
    TeacherProblemExplanationJobStatusView,
)
from .problem_review.views import (
    ProblemReviewExportCreateView,
    ProblemReviewExportStatusView,
    ProblemReviewFinalizeView,
    ProblemReviewReportCollectionView,
    ProblemReviewReportDetailView,
    ProblemReviewPublishView,
)
from .timer_download_view import TimerDownloadView
from apps.support.omr.route_dependencies import (
    ToolsOMRPreviewView,
    ToolsOMRPdfView,
)

urlpatterns = [
    path("ppt/generate/", PptGenerateView.as_view(), name="tools-ppt-generate"),
    path("problem-review/reports/", ProblemReviewReportCollectionView.as_view(), name="tools-problem-review-report-collection"),
    path("problem-review/reports/<uuid:report_id>/", ProblemReviewReportDetailView.as_view(), name="tools-problem-review-report-detail"),
    path("problem-review/reports/<uuid:report_id>/verification/", ProblemReviewFinalizeView.as_view(), name="tools-problem-review-finalize"),
    path("problem-review/reports/<uuid:report_id>/publication/", ProblemReviewPublishView.as_view(), name="tools-problem-review-publish"),
    path("problem-review/reports/<uuid:report_id>/exports/", ProblemReviewExportCreateView.as_view(), name="tools-problem-review-export-create"),
    path("problem-review/reports/<uuid:report_id>/exports/<str:job_id>/", ProblemReviewExportStatusView.as_view(), name="tools-problem-review-export-status"),
    path("problem-studio/transfer-document/", ProblemStudioTransferDocumentView.as_view(), name="tools-problem-studio-transfer-document"),
    path("problem-studio/beta-access/", ProblemStudioBetaAccessView.as_view(), name="tools-problem-studio-beta-access"),
    path("problem-studio/explanation-runs/", ProblemStudioExplanationRunCreateView.as_view(), name="tools-problem-studio-explanation-run-create"),
    path("problem-studio/explanation-runs/<uuid:run_id>/", ProblemStudioExplanationRunStatusView.as_view(), name="tools-problem-studio-explanation-run-status"),
    path("problem-studio/explanation-runs/<uuid:run_id>/resume/", ProblemStudioExplanationRunResumeView.as_view(), name="tools-problem-studio-explanation-run-resume"),
    path("problem-studio/transfer-jobs/", ProblemStudioTransferJobCreateView.as_view(), name="tools-problem-studio-transfer-job-create"),
    path("problem-studio/transfer-jobs/<str:job_id>/", ProblemStudioTransferJobStatusView.as_view(), name="tools-problem-studio-transfer-job-status"),
    path("problem-studio/transfer-jobs/<str:job_id>/hangul-handoff/", ProblemStudioHangulHandoffCreateView.as_view(), name="tools-problem-studio-hangul-handoff-create"),
    path("problem-studio/hangul-handoffs/<str:token>/", ProblemStudioHangulHandoffConsumeView.as_view(), name="tools-problem-studio-hangul-handoff-consume"),
    path("problem-studio/hangul-companion/", ProblemStudioHangulCompanionDownloadView.as_view(), name="tools-problem-studio-hangul-companion-download"),
    path("problem-studio/fonts/", ProblemStudioFontCollectionView.as_view(), name="tools-problem-studio-font-collection"),
    path("problem-studio/fonts/<uuid:font_id>/", ProblemStudioFontDetailView.as_view(), name="tools-problem-studio-font-detail"),
    path("problem-studio/document-style/", ProblemStudioDocumentStyleView.as_view(), name="tools-problem-studio-document-style"),
    path("problem-studio/voice-profiles/", ProblemStudioVoiceProfileCollectionView.as_view(), name="tools-problem-studio-voice-profile-collection"),
    path("problem-studio/voice-profiles/<uuid:profile_id>/", ProblemStudioVoiceProfileDetailView.as_view(), name="tools-problem-studio-voice-profile-detail"),
    path("problem-studio/voice-profiles/<uuid:profile_id>/samples/", ProblemStudioVoiceSampleCollectionView.as_view(), name="tools-problem-studio-voice-sample-collection"),
    path("problem-studio/jobs/", ProblemStudioJobCreateView.as_view(), name="tools-problem-studio-job-create"),
    path("problem-studio/jobs/<str:job_id>/", ProblemStudioJobStatusView.as_view(), name="tools-problem-studio-job-status"),
    path("problem-studio/jobs/<str:job_id>/reviews/", ProblemStudioGenerationReviewView.as_view(), name="tools-problem-studio-generation-review"),
    path("problem-solver/jobs/", TeacherProblemExplanationJobCreateView.as_view(), name="tools-problem-solver-job-create"),
    path("problem-solver/jobs/<str:job_id>/", TeacherProblemExplanationJobStatusView.as_view(), name="tools-problem-solver-job-status"),
    path("omr/preview/", ToolsOMRPreviewView.as_view(), name="tools-omr-preview"),
    path("omr/pdf/", ToolsOMRPdfView.as_view(), name="tools-omr-pdf"),
    path("timer/download/", TimerDownloadView.as_view(), name="tools-timer-download"),
]
