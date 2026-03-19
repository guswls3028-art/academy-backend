# PATH: apps/domains/exams/views/pdf_question_extract_view.py
# PDF 시험지 업로드 → AI 문항 분할 job 제출 API

import hashlib
import logging
import uuid

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.parsers import MultiPartParser

from apps.core.permissions import TenantResolvedAndStaff
from apps.domains.ai.gateway import dispatch_job
from apps.infrastructure.storage.r2 import upload_fileobj_to_r2_storage, generate_presigned_get_url_storage as generate_presigned_download_url

logger = logging.getLogger(__name__)


class PdfQuestionExtractView(APIView):
    """
    POST /exams/pdf-extract/
    - PDF 파일 업로드 → R2 저장 → question_segmentation AI job 제출
    - Returns: { job_id, status: "submitted" }
    """
    permission_classes = [TenantResolvedAndStaff]
    parser_classes = [MultiPartParser]

    def post(self, request):
        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response({"detail": "tenant required"}, status=status.HTTP_403_FORBIDDEN)

        pdf_file = request.FILES.get("file")
        if not pdf_file:
            return Response({"detail": "file is required"}, status=status.HTTP_400_BAD_REQUEST)

        # Validate file type
        name_lower = (pdf_file.name or "").lower()
        if not (name_lower.endswith(".pdf") or name_lower.endswith(".png") or name_lower.endswith(".jpg") or name_lower.endswith(".jpeg")):
            return Response({"detail": "PDF 또는 이미지 파일만 업로드 가능합니다."}, status=status.HTTP_400_BAD_REQUEST)

        # Max 50MB
        if pdf_file.size > 50 * 1024 * 1024:
            return Response({"detail": "파일 크기는 50MB 이하여야 합니다."}, status=status.HTTP_400_BAD_REQUEST)

        exam_id = request.data.get("exam_id")

        try:
            # Upload to R2
            name_hash = hashlib.md5(pdf_file.name.encode()).hexdigest()[:8]
            r2_key = f"tenants/{tenant.id}/exams/pdf-extract/{uuid.uuid4()}/{name_hash}_{pdf_file.name}"
            upload_fileobj_to_r2_storage(
                file_obj=pdf_file,
                key=r2_key,
                content_type=pdf_file.content_type or "application/pdf",
            )

            # Generate presigned download URL for worker
            download_url = generate_presigned_download_url(r2_key)

            # Submit AI job
            result = dispatch_job(
                job_type="question_segmentation",
                payload={
                    "download_url": download_url,
                    "tenant_id": str(tenant.id),
                    "exam_id": str(exam_id) if exam_id else None,
                    "filename": pdf_file.name,
                },
                tenant_id=str(tenant.id),
                source_domain="exams",
                source_id=str(exam_id) if exam_id else None,
                tier="basic",
            )

            return Response({
                "job_id": result.get("job_id"),
                "status": "submitted",
                "message": "PDF 문항 분할이 시작되었습니다.",
            }, status=status.HTTP_202_ACCEPTED)

        except Exception as e:
            logger.exception("PDF question extract failed: %s", e)
            return Response(
                {"detail": f"PDF 처리 중 오류: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
