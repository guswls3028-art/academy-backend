"""AI Job 진행률/상태 전용 endpoint (Redis 우선, Redis 없을 때 DB 폴백)"""
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.domains.ai.redis_status_cache import get_job_status_from_redis, cache_job_status
from academy.adapters.db.django.repositories_ai import get_job_model_for_status
from src.infrastructure.cache.redis_progress_adapter import RedisProgressAdapter


class JobProgressView(APIView):
    """Job 진행률/상태 조회 (Redis 우선, 없으면 DB 폴백으로 완료 상태 반환)"""

    permission_classes = [IsAuthenticated]

    def get(self, request, job_id: str):
        """GET /api/v1/jobs/{job_id}/progress/"""
        tenant = getattr(request, "tenant", None)

        if not tenant:
            return Response(
                {"detail": "tenant가 필요합니다."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        tenant_id = str(tenant.id)

        # ✅ Redis에서 상태 조회 (Tenant 네임스페이스)
        cached_status = get_job_status_from_redis(tenant_id, job_id)

        if not cached_status:
            # ✅ Redis에 없을 때 DB 폴백: 이미 DONE/FAILED면 완료 상태 반환 (진행 상황 위젯 정상 종료)
            job_model = get_job_model_for_status(job_id, tenant_id)
            if job_model and job_model.status in ("DONE", "FAILED", "REJECTED_BAD_INPUT", "FALLBACK_TO_GPU", "REVIEW_REQUIRED"):
                from apps.domains.ai.services.job_status_response import build_job_status_response
                response_data = build_job_status_response(job_model)
                # progress API 형식에 맞게 status만 상위로 (프론트가 status로 완료 판단)
                return Response({
                    "job_id": job_id,
                    "job_type": response_data.get("job_type"),
                    "status": job_model.status,
                    "progress": response_data.get("progress"),
                    "result": response_data.get("result"),
                    "error_message": getattr(job_model, "error_message", None) or response_data.get("error_message"),
                })
            # 진행 중이거나 미존재: UNKNOWN
            return Response(
                {"status": "UNKNOWN", "message": "진행 상태를 확인할 수 없습니다."},
                status=status.HTTP_200_OK,
            )

        job_status = cached_status.get("status")

        # ✅ 진행률은 Redis에서 조회 (tenant_id 전달 필수)
        progress = None
        if job_status == "RUNNING":
            progress_adapter = RedisProgressAdapter()
            progress = progress_adapter.get_progress(job_id, tenant_id=tenant_id)

        response_data = {
            "job_id": job_id,
            "job_type": cached_status.get("job_type"),
            "status": job_status,
            "progress": progress,
        }

        if job_status in ["DONE", "FAILED", "REJECTED_BAD_INPUT", "FALLBACK_TO_GPU", "REVIEW_REQUIRED"]:
            if "result" in cached_status:
                response_data["result"] = cached_status["result"]
            if job_status in ["FAILED", "REJECTED_BAD_INPUT", "FALLBACK_TO_GPU", "REVIEW_REQUIRED"]:
                response_data["error_message"] = cached_status.get("error_message")

        return Response(response_data)
