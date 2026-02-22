# PATH: apps/support/video/urls_internal.py

from __future__ import annotations

from django.urls import path

# ==================================================
# ✅ HTTP Polling 엔드포인트 제거됨 (SQS 기반 아키텍처로 전환)
#
# 제거된 엔드포인트:
# - /video-worker/next/ (VideoWorkerClaimNextView)
# - /video-worker/<video_id>/complete/ (VideoWorkerCompleteView)
# - /video-worker/<video_id>/fail/ (VideoWorkerFailView)
# - /video-worker/<video_id>/heartbeat/ (InternalVideoWorkerHeartbeatView)
#
# 새로운 아키텍처:
# - SQS 기반 큐 사용
# - Worker는 SQS Long Polling으로 작업 수신
# - 완료/실패는 repositories_video.job_complete() (Batch worker)
#
# Legacy 호환성:
# - /api/v1/videos/internal/videos/<video_id>/processing-complete/ 유지
#   (apps/support/video/urls.py에 정의됨)
# ==================================================

urlpatterns = [
    # HTTP polling 엔드포인트는 모두 제거됨
    # SQS 기반 아키텍처로 전환 완료
]
