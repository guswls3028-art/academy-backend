# PATH: apps/domains/ai/urls.py
from django.urls import path

from apps.domains.ai.views.job_status_view import JobStatusView
from apps.domains.ai.views.job_progress_view import JobProgressView

# ==================================================
# 공개(인증) 엔드포인트: 엑셀 내보내기 등 job 상태 조회
# ==================================================

urlpatterns = [
    path("<str:job_id>/", JobStatusView.as_view(), name="job-status"),
    # ✅ Progress endpoint 추가 (Redis-only)
    path("<str:job_id>/progress/", JobProgressView.as_view(), name="job-progress"),
]
