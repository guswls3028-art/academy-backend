"""AI Job 진행률/상태 전용 endpoint (Redis-only)"""
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.domains.ai.redis_status_cache import get_job_status_from_redis
from src.infrastructure.cache.redis_progress_adapter import RedisProgressAdapter


class JobProgressView(APIView):
    """Job 진행률/상태 조회 (Redis-only, DB 부하 0)"""
    
    permission_classes = [IsAuthenticated]
    
    def get(self, request, job_id: str):
        """GET /api/v1/jobs/{job_id}/progress/"""
        tenant = getattr(request, "tenant", None)
        
        if not tenant:
            return Response(
                {"detail": "tenant가 필요합니다."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        
        # ✅ Redis에서 상태 조회 (Tenant 네임스페이스)
        cached_status = get_job_status_from_redis(str(tenant.id), job_id)
        
        if not cached_status:
            # ✅ Redis에 없으면 UNKNOWN 상태 반환 (404는 UX상 위험)
            # TTL 만료되었지만 아직 DONE/FAILED 상태일 수 있음
            return Response(
                {"status": "UNKNOWN", "message": "진행 상태를 확인할 수 없습니다."},
                status=status.HTTP_200_OK,
            )
        
        job_status = cached_status.get("status")
        
        # ✅ 진행률은 Redis에서 조회 (tenant_id 전달 필수)
        progress = None
        # AI Job status는 "RUNNING"이 실제 처리 중 상태임
        if job_status == "RUNNING":
            # ✅ tenant_id 전달하여 tenant namespace 키 조회
            progress_adapter = RedisProgressAdapter()
            progress = progress_adapter.get_progress(job_id, tenant_id=str(tenant.id))
        
        # ✅ 응답 구성
        response_data = {
            "job_id": job_id,
            "job_type": cached_status.get("job_type"),
            "status": job_status,
            "progress": progress,
        }
        
        # ✅ 완료 상태면 result/error 포함
        # AI Job status는 "DONE", "FAILED", "REJECTED_BAD_INPUT", "FALLBACK_TO_GPU", "REVIEW_REQUIRED" 등이 있음
        if job_status in ["DONE", "FAILED", "REJECTED_BAD_INPUT", "FALLBACK_TO_GPU", "REVIEW_REQUIRED"]:
            if "result" in cached_status:
                response_data["result"] = cached_status["result"]
            if job_status in ["FAILED", "REJECTED_BAD_INPUT", "FALLBACK_TO_GPU", "REVIEW_REQUIRED"]:
                response_data["error_message"] = cached_status.get("error_message")
        
        return Response(response_data)
