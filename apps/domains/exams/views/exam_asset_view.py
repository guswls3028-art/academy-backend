from __future__ import annotations

import mimetypes
import uuid

from django.db.models import Q
from django.shortcuts import get_object_or_404
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from rest_framework.exceptions import ValidationError

from apps.domains.exams.models import Exam, ExamAsset
from apps.domains.exams.serializers.exam_asset import ExamAssetSerializer
from apps.domains.exams.services.template_resolver import resolve_structure_exam, assert_template_editable
from apps.domains.exams.services.structure_copy_service import ensure_regular_exam_owns_structure
from apps.core.r2_paths import ai_exam_asset_key
from apps.infrastructure.storage.r2 import upload_fileobj_to_r2
from apps.core.permissions import TenantResolvedAndStaff


class ExamAssetView(APIView):
    """
    ExamAsset API (봉인)

    - GET: 로그인만, regular → structure owner resolve
    - POST: Teacher/Admin + editable structure owner
    - regular는 자기 복사본에 업로드하고 template 원본은 오염시키지 않는다.
    """

    def get_permissions(self):
        if self.request.method == "GET":
            return [IsAuthenticated()]
        return [IsAuthenticated(), TenantResolvedAndStaff()]

    def get(self, request, exam_id: int):
        tenant = request.tenant
        exam = get_object_or_404(
            Exam.objects.filter(
                Q(sessions__lecture__tenant=tenant)
                | Q(derived_exams__sessions__lecture__tenant=tenant)
                | Q(tenant=tenant)
            ).distinct(),
            id=int(exam_id),
        )
        template = resolve_structure_exam(exam)

        qs = ExamAsset.objects.filter(exam=template).order_by("asset_type")
        return Response(ExamAssetSerializer(qs, many=True).data)

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
        exam = resolve_structure_exam(exam)

        assert_template_editable(exam)

        asset_type = request.data.get("asset_type")
        upload_file = request.FILES.get("file")
        if not asset_type or not upload_file:
            return Response({"detail": "asset_type and file are required"}, status=400)

        valid = {t for t, _ in ExamAsset.AssetType.choices}
        if asset_type not in valid:
            raise ValidationError({"asset_type": f"must be one of {sorted(valid)}"})

        tenant_id = getattr(request, "tenant", None) and request.tenant.id
        if not tenant_id:
            return Response({"detail": "tenant required for upload"}, status=status.HTTP_400_BAD_REQUEST)
        name = upload_file.name or ""
        ext = name.split(".")[-1] if "." in name else "bin"
        key = ai_exam_asset_key(
            tenant_id=tenant_id,
            exam_id=exam.id,
            asset_type=asset_type,
            unique_id=uuid.uuid4().hex,
            ext=ext,
        )

        upload_fileobj_to_r2(
            fileobj=upload_file,
            key=key,
            content_type=upload_file.content_type,
        )

        obj, _ = ExamAsset.objects.update_or_create(
            exam=exam,
            asset_type=asset_type,
            defaults={
                "file_key": key,
                "file_type": upload_file.content_type or mimetypes.guess_type(upload_file.name)[0],
                "file_size": upload_file.size,
            },
        )

        return Response(ExamAssetSerializer(obj).data, status=status.HTTP_201_CREATED)
