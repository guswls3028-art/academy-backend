# apps/domains/exams/views/exam_image_upload_view.py
"""
문항/해설 이미지 업로드 API.

POST /exams/<exam_id>/upload-image/
- 강사가 수동으로 문제 이미지 또는 해설 이미지를 업로드
- R2 Storage 버킷에 저장 후 image_key 반환
- 프론트에서 image_key를 받아 해설 저장 시 함께 전송
"""
from __future__ import annotations

import uuid

from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import TenantResolvedAndStaff
from apps.domains.exams.models import Exam
from apps.domains.exams.services.template_resolver import (
    assert_template_editable,
    resolve_structure_exam,
)
from apps.domains.exams.services.structure_copy_service import ensure_regular_exam_owns_structure
from apps.infrastructure.storage.r2 import (
    upload_fileobj_to_r2_storage,
    generate_presigned_get_url_storage,
)

ALLOWED_TYPES = {"image/png", "image/jpeg", "image/jpg", "image/webp"}
MAX_SIZE = 10 * 1024 * 1024  # 10MB
MAX_PIXELS = 20_000_000
ALLOWED_IMAGE_FORMATS = {"PNG": "png", "JPEG": "jpg", "WEBP": "webp"}


class ExamImageUploadView(APIView):
    """문항/해설 이미지 R2 업로드 → image_key + image_url 반환."""

    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def post(self, request, exam_id: int):
        tenant = request.tenant
        exam = get_object_or_404(
            Exam.objects.filter(
                Q(sessions__lecture__tenant=tenant)
                | Q(derived_exams__sessions__lecture__tenant=tenant)
                | Q(tenant=tenant)
            ).distinct(),
            id=int(exam_id),
        )
        ensure_regular_exam_owns_structure(exam)
        template = resolve_structure_exam(exam)
        assert_template_editable(template)

        upload_file = request.FILES.get("file")
        if not upload_file:
            return Response(
                {"detail": "file is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        content_type = upload_file.content_type or ""
        if content_type not in ALLOWED_TYPES:
            return Response(
                {"detail": f"허용되지 않는 파일 형식입니다. ({content_type})"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if upload_file.size > MAX_SIZE:
            return Response(
                {"detail": "파일 크기는 10MB 이하여야 합니다."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            from PIL import Image

            with Image.open(upload_file) as image:
                image_format = str(image.format or "").upper()
                width, height = image.size
                if image_format not in ALLOWED_IMAGE_FORMATS:
                    raise ValueError("unsupported image format")
                if width < 1 or height < 1 or width * height > MAX_PIXELS:
                    raise ValueError("image pixel limit exceeded")
                image.verify()
        except Exception:
            return Response(
                {"detail": "정상적인 PNG, JPG 또는 WEBP 이미지만 올릴 수 있습니다."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        finally:
            upload_file.seek(0)

        ext = ALLOWED_IMAGE_FORMATS[image_format]
        unique_id = uuid.uuid4().hex[:12]
        image_key = f"tenants/{tenant.id}/exams/images/{template.id}/{unique_id}.{ext}"

        upload_fileobj_to_r2_storage(
            fileobj=upload_file,
            key=image_key,
            content_type=content_type,
        )

        image_url = generate_presigned_get_url_storage(
            key=image_key,
            expires_in=3600,
        )

        return Response(
            {"image_key": image_key, "image_url": image_url},
            status=status.HTTP_201_CREATED,
        )
