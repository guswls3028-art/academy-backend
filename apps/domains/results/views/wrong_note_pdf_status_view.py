# apps/domains/results/views/wrong_note_pdf_status_view.py
from __future__ import annotations

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import PermissionDenied, NotFound

from apps.core.permissions import TenantResolvedAndStaff
from apps.domains.results.models import WrongNotePDF
from apps.domains.results.serializers.wrong_note_pdf_serializers import (
    WrongNotePDFStatusSerializer,
)
from apps.infrastructure.storage.r2 import generate_presigned_get_url_storage
from apps.support.results.admin_exam_dependencies import (
    enrollment_exists_for_tenant,
)


class WrongNotePDFStatusView(APIView):
    """
    STEP 3-2: 오답노트 PDF Job 상태 조회 (polling)

    GET /results/wrong-notes/pdf/<job_id>/

    🔐 보안:
    - 교사/관리자: 현재 테넌트의 job만 조회 가능
    """

    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def _assert_enrollment_access(self, request, enrollment_id: int) -> None:
        if not enrollment_exists_for_tenant(enrollment_id=int(enrollment_id), tenant=request.tenant):
            raise PermissionDenied("You cannot access this PDF job.")

    def get(self, request, job_id: int):
        job = WrongNotePDF.objects.filter(id=int(job_id)).first()
        if not job:
            raise NotFound("job not found")

        self._assert_enrollment_access(request, int(job.enrollment_id))

        # DONE이면 R2 attachment URL 제공. API 컨테이너의 local default_storage는
        # 배포 교체 시 사라지므로 다운로드 정본으로 사용할 수 없다.
        file_url = ""
        if job.status == WrongNotePDF.Status.DONE and job.file_path:
            try:
                file_url = generate_presigned_get_url_storage(
                    key=job.file_path,
                    expires_in=3600,
                    filename=f"wrong-note-{job.id}.pdf",
                    content_type="application/pdf",
                )
            except Exception:
                file_url = ""

        data = {
            "job_id": int(job.id),
            "status": str(job.status),
            "file_path": str(job.file_path or ""),
            "file_url": str(file_url or ""),
            "error_message": str(job.error_message or ""),
            "created_at": job.created_at,
            "updated_at": job.updated_at,
        }

        return Response(WrongNotePDFStatusSerializer(data).data)
