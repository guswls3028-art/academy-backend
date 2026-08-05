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

        explanation_file = request.FILES.get("explanation_file")
        if explanation_file is not None:
            explanation_name = str(explanation_file.name or "").lower()
            if not name_lower.endswith((".pdf", ".png", ".jpg", ".jpeg")):
                return Response(
                    {
                        "detail": (
                            "선생님 해설 HWP를 함께 올릴 때 문제지는 "
                            "답 표시가 없는 PDF나 이미지여야 합니다."
                        )
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if not explanation_name.endswith((".hwp", ".hwpx")):
                return Response(
                    {"detail": "선생님 해설은 HWP 또는 HWPX 파일만 함께 올릴 수 있습니다."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if explanation_file.size > 50 * 1024 * 1024:
                return Response(
                    {"detail": "선생님 해설 파일은 50MB 이하여야 합니다."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

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

            explanation_key = ""
            explanation_download_url = ""
            if explanation_file is not None:
                explanation_name_hash = hashlib.md5(
                    explanation_file.name.encode()
                ).hexdigest()[:8]
                explanation_key = (
                    f"tenants/{tenant.id}/exams/pdf-extract/{uuid.uuid4()}/"
                    f"{explanation_name_hash}_{explanation_file.name}"
                )
                upload_fileobj_to_r2_storage(
                    fileobj=explanation_file,
                    key=explanation_key,
                    content_type=(
                        explanation_file.content_type or "application/x-hwp"
                    ),
                )
                explanation_download_url = generate_presigned_download_url(
                    key=explanation_key
                )

            if exam is not None:
                source_defaults = {
                    "file_key": r2_key,
                    "file_type": pdf_file.content_type or "",
                    "file_size": int(pdf_file.size or 0),
                }
                ExamAsset.objects.update_or_create(
                    exam=exam,
                    asset_type=ExamAsset.AssetType.PROBLEM_SOURCE,
                    defaults=source_defaults,
                )
                if name_lower.endswith(".pdf"):
                    # Keep the established distribution asset contract without
                    # requiring the browser to upload the same PDF twice.
                    ExamAsset.objects.update_or_create(
                        exam=exam,
                        asset_type=ExamAsset.AssetType.PROBLEM_PDF,
                        defaults=source_defaults,
                    )
                if explanation_file is not None:
                    ExamAsset.objects.update_or_create(
                        exam=exam,
                        asset_type=(
                            ExamAsset.AssetType.TEACHER_EXPLANATION_SOURCE
                        ),
                        defaults={
                            "file_key": explanation_key,
                            "file_type": explanation_file.content_type or "",
                            "file_size": int(explanation_file.size or 0),
                        },
                    )
                exam.source_filename = str(pdf_file.name or "")[:255]

            # HWP/HWPX 미주 원본 이미지는 그대로 분리한다. 모든 문항에 완전한
            # 미주 이미지가 없으면 worker가 성공으로 오인하지 않고 PDF를 요청한다.
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
                    "explanation_download_url": explanation_download_url,
                    "explanation_filename": (
                        explanation_file.name if explanation_file is not None else ""
                    ),
                },
                tenant_id=str(tenant.id),
                source_domain="exams",
                source_id=str(exam_id) if exam_id else None,
                tier="basic",
            )

            return Response({
                "job_id": result.get("job_id"),
                "status": "submitted",
                "message": "자료 유형을 확인한 뒤 문항과 원본 해설 분리를 시작합니다.",
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
