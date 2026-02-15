# PATH: apps/domains/ai/views/job_status_view.py
# GET /api/v1/jobs/<job_id>/ — 엑셀 내보내기·엑셀 파싱 등 AI job 상태·결과 조회 (tenant-scoped)

from __future__ import annotations

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from academy.adapters.db.django.repositories_ai import DjangoAIJobRepository
from apps.domains.ai.services.job_status_response import build_job_status_response


def _ai_repo():
    return DjangoAIJobRepository()


class JobStatusView(APIView):
    """
    GET /api/v1/jobs/<job_id>/
    응답: { "job_id", "job_type", "status", "result"?, "error_message"?, "progress"? }
    - result.download_url: 엑셀 내보내기 완료 시 다운로드 URL (presigned)
    - result.filename: 권장 파일명
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, job_id: str):
        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response(
                {"detail": "tenant가 필요합니다."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        repo = _ai_repo()
        job = repo.get_job_model_for_status(job_id, str(tenant.id))
        if not job:
            return Response(
                {"detail": "해당 job을 찾을 수 없습니다."},
                status=status.HTTP_404_NOT_FOUND,
            )
        result_payload = repo.get_result_payload_for_job(job)
        return Response(build_job_status_response(job, result_payload=result_payload))
