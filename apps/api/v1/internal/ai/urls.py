# ==================================================
# ✅ HTTP Polling 엔드포인트 제거됨 (SQS 기반 아키텍처로 전환)
#
# 제거된 엔드포인트:
# - /api/v1/internal/ai/job/next/
# - /api/v1/internal/ai/job/result/
#
# 새로운 아키텍처:
# - SQS 기반 큐 사용
# - Worker는 SQS Long Polling으로 작업 수신
# - 완료/실패는 AISQSQueue.complete_job() / fail_job() 사용
# ==================================================

from django.urls import path

# 레거시 뷰는 유지하되 사용 안 함 (호환성)
# from apps.domains.ai.views.internal_ai_job_view import (
#     InternalAIJobNextView,
#     InternalAIJobResultView,
# )

urlpatterns = [
    # HTTP polling 엔드포인트는 모두 제거됨
    # SQS 기반 아키텍처로 전환 완료
]
