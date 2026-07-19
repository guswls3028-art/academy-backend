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
    ProblemStudioHangulHandoffCreateView,
    ProblemStudioHangulHandoffConsumeView,
    ProblemStudioHangulCompanionDownloadView,
)
from .timer_download_view import TimerDownloadView
from apps.support.omr.route_dependencies import (
    ToolsOMRPreviewView,
    ToolsOMRPdfView,
)

urlpatterns = [
    path("ppt/generate/", PptGenerateView.as_view(), name="tools-ppt-generate"),
    path("problem-studio/transfer-document/", ProblemStudioTransferDocumentView.as_view(), name="tools-problem-studio-transfer-document"),
    path("problem-studio/transfer-jobs/", ProblemStudioTransferJobCreateView.as_view(), name="tools-problem-studio-transfer-job-create"),
    path("problem-studio/transfer-jobs/<str:job_id>/", ProblemStudioTransferJobStatusView.as_view(), name="tools-problem-studio-transfer-job-status"),
    path("problem-studio/transfer-jobs/<str:job_id>/hangul-handoff/", ProblemStudioHangulHandoffCreateView.as_view(), name="tools-problem-studio-hangul-handoff-create"),
    path("problem-studio/hangul-handoffs/<str:token>/", ProblemStudioHangulHandoffConsumeView.as_view(), name="tools-problem-studio-hangul-handoff-consume"),
    path("problem-studio/hangul-companion/", ProblemStudioHangulCompanionDownloadView.as_view(), name="tools-problem-studio-hangul-companion-download"),
    path("problem-studio/jobs/", ProblemStudioJobCreateView.as_view(), name="tools-problem-studio-job-create"),
    path("problem-studio/jobs/<str:job_id>/", ProblemStudioJobStatusView.as_view(), name="tools-problem-studio-job-status"),
    path("omr/preview/", ToolsOMRPreviewView.as_view(), name="tools-omr-preview"),
    path("omr/pdf/", ToolsOMRPdfView.as_view(), name="tools-omr-pdf"),
    path("timer/download/", TimerDownloadView.as_view(), name="tools-timer-download"),
]
