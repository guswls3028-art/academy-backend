# PATH: apps/core/views/job_progress.py
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from academy.adapters.db.django import repositories_ai as ai_repo


# --------------------------------------------------
# Worker job progress (Redis) — 우하단 실시간 프로그래스바용
# --------------------------------------------------


class JobProgressView(APIView):
    """
    GET /api/v1/core/job_progress/<job_id>/
    Redis에 기록된 워커 진행률 조회. tenant 소속 job만 허용.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, job_id: str):
        from academy.adapters.cache.redis_progress_adapter import RedisProgressAdapter

        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response({"detail": "tenant가 필요합니다."}, status=400)
        job = ai_repo.get_job_model_for_status(job_id, str(tenant.id))
        if not job:
            return Response({"detail": "해당 작업을 찾을 수 없습니다."}, status=404)
        # ✅ tenant_id 전달 필수 (tenant namespace 키 사용)
        progress = RedisProgressAdapter().get_progress(job_id, tenant_id=str(tenant.id))
        if not progress:
            return Response({"step": None, "percent": None})
        return Response({
            "step": progress.get("step"),
            "percent": progress.get("percent"),
            **{k: v for k, v in progress.items() if k not in ("step", "percent")},
        })
