from __future__ import annotations

import mimetypes
import uuid

from django.shortcuts import get_object_or_404
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from rest_framework.exceptions import ValidationError

from apps.domains.exams.models import Exam, ExamAsset
from apps.domains.exams.serializers.exam_asset import ExamAssetSerializer
from apps.domains.exams.services.template_resolver import resolve_template_exam, assert_template_editable
from apps.infrastructure.storage.r2 import upload_fileobj_to_r2
from apps.domains.results.permissions import IsTeacherOrAdmin


class ExamAssetView(APIView):
    """
    ExamAsset API (봉인)

    - GET: 로그인만, regular → template resolve
    - POST: Teacher/Admin + template only
    - template이 regular에 의해 사용 중이면 자산 교체 금지(운영 사고 차단)
    """

    def get_permissions(self):
        if self.request.method == "GET":
            return [IsAuthenticated()]
        return [IsAuthenticated(), IsTeacherOrAdmin()]

    def get(self, request, exam_id: int):
        exam = get_object_or_404(Exam, id=int(exam_id))
        template = resolve_template_exam(exam)

        qs = ExamAsset.objects.filter(exam=template).order_by("asset_type")
        return Response(ExamAssetSerializer(qs, many=True).data)

    def post(self, request, exam_id: int):
        exam = get_object_or_404(Exam, id=int(exam_id))
        if exam.exam_type != Exam.ExamType.TEMPLATE:
            return Response({"detail": "Assets can be uploaded only to template exams."}, status=403)

        # template이 사용 중이면 구조 봉인 (asset 포함)
        assert_template_editable(exam)

        asset_type = request.data.get("asset_type")
        upload_file = request.FILES.get("file")
        if not asset_type or not upload_file:
            return Response({"detail": "asset_type and file are required"}, status=400)

        valid = {t for t, _ in ExamAsset.AssetType.choices}
        if asset_type not in valid:
            raise ValidationError({"asset_type": f"must be one of {sorted(valid)}"})

        name = upload_file.name or ""
        ext = name.split(".")[-1] if "." in name else "bin"
        key = f"exams/{exam.id}/assets/{asset_type}/{uuid.uuid4().hex}.{ext}"

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
