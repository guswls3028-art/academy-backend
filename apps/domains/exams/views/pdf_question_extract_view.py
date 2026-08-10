# PATH: apps/domains/exams/views/pdf_question_extract_view.py
# 시험 자료 원본 업로드 → 지원 형식 AI 문항 분할 job 제출 API

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
from apps.domains.exams.services.source_upload_policy import (
    AUTO_PAIR_EXPLANATION_SUFFIXES,
    AUTO_PAIR_PRIMARY_SUFFIXES,
    AUTO_SEGMENT_SUFFIXES,
    storage_content_type,
    validate_source_upload,
)
from apps.support.exams.view_dependencies import dispatch_ai_job, pdf_extract_exam_validation_error
from apps.infrastructure.storage.r2 import (
    upload_fileobj_to_r2_storage,
    generate_presigned_get_url_storage as generate_presigned_download_url,
)

logger = logging.getLogger(__name__)


class PdfQuestionExtractView(APIView):
    """
    POST /exams/pdf-extract/
    - 안전한 원본은 형식 그대로 R2와 시험 자산에 보존
    - 지원 형식은 question_segmentation job 제출, 나머지는 직접 검수 상태로 저장
    """

    permission_classes = [TenantResolvedAndStaff]
    parser_classes = [MultiPartParser]

    def post(self, request):
        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response({"detail": "tenant required"}, status=status.HTTP_403_FORBIDDEN)

        source_file = request.FILES.get("file")
        if not source_file:
            return Response({"detail": "file is required"}, status=status.HTTP_400_BAD_REQUEST)

        source_filename, source_suffix, source_error = validate_source_upload(source_file)
        if source_error:
            return Response(
                {"detail": source_error},
                status=status.HTTP_400_BAD_REQUEST,
            )

        explanation_file = request.FILES.get("explanation_file")
        explanation_filename = ""
        explanation_suffix = ""
        if explanation_file is not None:
            (
                explanation_filename,
                explanation_suffix,
                explanation_error,
            ) = validate_source_upload(explanation_file)
            if explanation_error:
                return Response(
                    {"detail": explanation_error},
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

        can_auto_segment = source_suffix in AUTO_SEGMENT_SUFFIXES
        if not can_auto_segment and exam is None:
            return Response(
                {"detail": ("이 형식의 원본을 보관하려면 먼저 시험을 선택해 주세요.")},
                status=status.HTTP_400_BAD_REQUEST,
            )
        can_auto_pair = bool(
            explanation_file is not None
            and source_suffix in AUTO_PAIR_PRIMARY_SUFFIXES
            and explanation_suffix in AUTO_PAIR_EXPLANATION_SUFFIXES
        )

        try:
            # Upload to R2
            name_hash = hashlib.md5(source_filename.encode()).hexdigest()[:8]
            r2_key = f"tenants/{tenant.id}/exams/pdf-extract/{uuid.uuid4()}/{name_hash}_{source_filename}"
            upload_fileobj_to_r2_storage(
                fileobj=source_file,
                key=r2_key,
                content_type=storage_content_type(source_file, source_suffix),
            )

            explanation_key = ""
            explanation_download_url = ""
            if explanation_file is not None:
                explanation_name_hash = hashlib.md5(explanation_filename.encode()).hexdigest()[:8]
                explanation_key = (
                    f"tenants/{tenant.id}/exams/pdf-extract/{uuid.uuid4()}/"
                    f"{explanation_name_hash}_{explanation_filename}"
                )
                upload_fileobj_to_r2_storage(
                    fileobj=explanation_file,
                    key=explanation_key,
                    content_type=storage_content_type(
                        explanation_file,
                        explanation_suffix,
                    ),
                )
                if can_auto_pair:
                    explanation_download_url = generate_presigned_download_url(key=explanation_key)

            if exam is not None:
                source_defaults = {
                    "file_key": r2_key,
                    "file_type": source_file.content_type or "",
                    "file_size": int(source_file.size or 0),
                }
                ExamAsset.objects.update_or_create(
                    exam=exam,
                    asset_type=ExamAsset.AssetType.PROBLEM_SOURCE,
                    defaults=source_defaults,
                )
                if source_suffix == ".pdf":
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
                        asset_type=(ExamAsset.AssetType.TEACHER_EXPLANATION_SOURCE),
                        defaults={
                            "file_key": explanation_key,
                            "file_type": explanation_file.content_type or "",
                            "file_size": int(explanation_file.size or 0),
                        },
                    )
                exam.source_filename = source_filename[:255]

            if not can_auto_segment:
                exam.segmentation_status = Exam.SegmentationStatus.CONVERSION_REQUIRED
                exam.save(
                    update_fields=[
                        "source_filename",
                        "segmentation_status",
                        "updated_at",
                    ]
                )
                return Response(
                    {
                        "job_id": None,
                        "status": "source_saved",
                        "processing_started": False,
                        "message": (
                            "원본 형식 그대로 저장했습니다. 이 형식은 자동 문항 "
                            "분리 대신 시험 상세에서 문항과 해설을 직접 등록해 "
                            "검수할 수 있습니다. PDF 재업로드는 필수가 아닙니다."
                        ),
                    },
                    status=status.HTTP_202_ACCEPTED,
                )

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
                    "filename": source_filename,
                    "explanation_download_url": explanation_download_url,
                    "explanation_filename": (explanation_filename if can_auto_pair else ""),
                },
                tenant_id=str(tenant.id),
                source_domain="exams",
                source_id=str(exam_id) if exam_id else None,
                tier="basic",
            )

            message = "자료 유형을 확인한 뒤 문항과 원본 해설 분리를 시작합니다."
            if explanation_file is not None and not can_auto_pair:
                message = (
                    "두 원본을 형식 그대로 저장했습니다. 문제 자료의 자동 문항 "
                    "분리를 시작하며, 해설 원본은 시험 상세에서 직접 연결해 "
                    "검수할 수 있습니다."
                )
            return Response(
                {
                    "job_id": result.get("job_id"),
                    "status": "submitted",
                    "processing_started": True,
                    "message": message,
                },
                status=status.HTTP_202_ACCEPTED,
            )

        except Exception as e:
            logger.exception("Exam source processing failed: %s", e)
            if exam is not None:
                Exam.objects.filter(id=exam.id, tenant=tenant).update(
                    segmentation_status=Exam.SegmentationStatus.FAILED,
                )
            detail = f"자료 처리 중 오류: {str(e)}" if settings.DEBUG else "자료 처리 중 오류가 발생했습니다."
            return Response(
                {"detail": detail},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
