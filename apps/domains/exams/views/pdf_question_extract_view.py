# PATH: apps/domains/exams/views/pdf_question_extract_view.py
# PDF 시험지 업로드 → AI 문항 분할 job 제출 API

import hashlib
import logging
import uuid

from django.conf import settings
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.parsers import MultiPartParser

from apps.core.permissions import TenantResolvedAndStaff
from apps.domains.exams.models import Exam, ExamAsset
from apps.support.exams.view_dependencies import dispatch_ai_job, pdf_extract_exam_validation_error
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
        supported_suffixes = (".pdf", ".png", ".jpg", ".jpeg", ".hwp", ".hwpx")
        if not name_lower.endswith(supported_suffixes):
            return Response(
                {"detail": "PDF, 이미지, HWP/HWPX 파일만 업로드 가능합니다."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Max 50MB
        if pdf_file.size > 50 * 1024 * 1024:
            return Response({"detail": "파일 크기는 50MB 이하여야 합니다."}, status=status.HTTP_400_BAD_REQUEST)

        exam_id = request.data.get("exam_id")

        exam = None
        if exam_id:
            try:
                exam_id = int(exam_id)
            except (TypeError, ValueError):
                return Response(
                    {"detail": "시험 번호를 확인해 주세요."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            validation_error = pdf_extract_exam_validation_error(
                tenant=tenant,
                exam_id=exam_id,
            )
            if validation_error == "not_found":
                return Response(
                    {"detail": "시험을 찾을 수 없습니다."},
                    status=status.HTTP_404_NOT_FOUND,
                )
            if validation_error == "processing":
                return Response(
                    {"detail": "이 시험지는 이미 문항을 분리하고 있습니다."},
                    status=status.HTTP_409_CONFLICT,
                )
            if validation_error == "regular_locked":
                return Response(
                    {
                        "detail": (
                            "문항이나 성적이 있는 운영 시험의 원본은 자동으로 "
                            "덮어쓸 수 없습니다. 새 시험을 만들거나 기존 문항을 "
                            "직접 수정해 주세요."
                        )
                    },
                    status=status.HTTP_409_CONFLICT,
                )
            exam = Exam.objects.get(id=exam_id, tenant=tenant)

        try:
            # Upload to R2
            name_hash = hashlib.md5(pdf_file.name.encode()).hexdigest()[:8]
            r2_key = f"tenants/{tenant.id}/exams/pdf-extract/{uuid.uuid4()}/{name_hash}_{pdf_file.name}"
            upload_fileobj_to_r2_storage(
                fileobj=pdf_file,
                key=r2_key,
                content_type=pdf_file.content_type or "application/pdf",
            )

            if exam is not None:
                ExamAsset.objects.update_or_create(
                    exam=exam,
                    asset_type=ExamAsset.AssetType.PROBLEM_SOURCE,
                    defaults={
                        "file_key": r2_key,
                        "file_type": pdf_file.content_type or "",
                        "file_size": int(pdf_file.size or 0),
                    },
                )
                exam.source_filename = str(pdf_file.name or "")[:255]

            if name_lower.endswith(".hwpx"):
                if exam is not None:
                    exam.segmentation_status = (
                        Exam.SegmentationStatus.CONVERSION_REQUIRED
                    )
                    exam.save(
                        update_fields=[
                            "source_filename",
                            "segmentation_status",
                            "updated_at",
                        ]
                    )
                return Response(
                    {
                        "status": "conversion_required",
                        "message": (
                            "원본은 안전하게 보관했습니다. HWP/HWPX는 수식과 "
                            "쪽 배치를 보존하기 위해 PDF로 저장한 파일을 한 번 더 "
                            "올려 주세요."
                        ),
                    },
                    status=status.HTTP_202_ACCEPTED,
                )

            # HWP 5.x 미주 해설 이미지는 원본 그대로 분리한다. HWPX는 렌더링
            # 보존 경계가 달라 현재 PDF 변환을 요청한다.
            # Generate presigned download URL for worker
            download_url = generate_presigned_download_url(key=r2_key)

            if exam is not None:
                exam.segmentation_status = Exam.SegmentationStatus.PROCESSING
                exam.save(
                    update_fields=[
                        "source_filename",
                        "segmentation_status",
                        "updated_at",
                    ]
                )

            # Submit AI job
            result = dispatch_ai_job(
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
                "message": "문항과 선생님 원본 해설 분리가 시작되었습니다.",
            }, status=status.HTTP_202_ACCEPTED)

        except Exception as e:
            logger.exception("PDF question extract failed: %s", e)
            if exam is not None:
                Exam.objects.filter(id=exam.id, tenant=tenant).update(
                    segmentation_status=Exam.SegmentationStatus.FAILED,
                )
            detail = (
                f"PDF 처리 중 오류: {str(e)}" if settings.DEBUG else "PDF 처리 중 오류가 발생했습니다."
            )
            return Response(
                {"detail": detail},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
