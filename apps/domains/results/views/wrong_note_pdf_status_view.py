# apps/domains/results/views/wrong_note_pdf_status_view.py
from __future__ import annotations

from django.core.files.storage import default_storage

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.exceptions import PermissionDenied, NotFound

from apps.domains.results.permissions import is_teacher_user
from apps.domains.enrollment.models import Enrollment
from apps.domains.results.models import WrongNotePDF
from apps.domains.results.serializers.wrong_note_pdf_serializers import (
    WrongNotePDFStatusSerializer,
)


class WrongNotePDFStatusView(APIView):
    """
    STEP 3-2: 오답노트 PDF Job 상태 조회 (polling)

    GET /results/wrong-notes/pdf/<job_id>/

    🔐 보안:
    - 학생: 본인 enrollment_id의 job만 조회 가능
    - 교사/관리자: 전체 조회 가능
    """

    permission_classes = [IsAuthenticated]

    def _assert_enrollment_access(self, request, enrollment_id: int) -> None:
        user = request.user

        # ✅ tenant isolation: always verify enrollment belongs to tenant
        qs = Enrollment.objects.filter(id=int(enrollment_id), tenant=request.tenant)
        if not qs.exists():
            raise PermissionDenied("You cannot access this PDF job.")

        if is_teacher_user(user):
            return

        if hasattr(Enrollment, "user_id"):
            qs = qs.filter(user_id=user.id)
        elif hasattr(Enrollment, "student_id"):
            qs = qs.filter(student_id=user.id)

        if not qs.exists():
            raise PermissionDenied("You cannot access this PDF job.")

    def get(self, request, job_id: int):
        job = WrongNotePDF.objects.filter(id=int(job_id)).first()
        if not job:
            raise NotFound("job not found")

        self._assert_enrollment_access(request, int(job.enrollment_id))

        # DONE이면 다운로드 URL 제공 (storage에 따라 url()이 실패할 수 있으니 방어)
        file_url = ""
        if job.file_path:
            try:
                file_url = default_storage.url(job.file_path)
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
