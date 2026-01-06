# apps/domains/exams/views/exam_asset_view.py
from __future__ import annotations

import mimetypes
import uuid

from django.shortcuts import get_object_or_404

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from apps.domains.exams.models import Exam, ExamAsset
from apps.domains.exams.serializers.exam_asset import ExamAssetSerializer
from apps.infrastructure.storage.r2 import upload_fileobj_to_r2

# - Teacher/Admin만 업로드 가능하도록 유지
from apps.domains.results.permissions import IsTeacherOrAdmin


class ExamAssetView(APIView):
    """
    시험 배포용 파일 업로드/목록

    GET  /exams/<exam_id>/assets/
    POST /exams/<exam_id>/assets/   (multipart: file, asset_type)

    ✅ 정책:
    - asset_type별로 1개만 유지 (update_or_create)
    - 업로드는 R2로 바로 올리고 file_key를 저장
    - download_url은 serializer에서 presigned GET으로 제공

    👍 권장 개선 (A)
    - 운영에서 학생이 문제PDF/OMR을 다운로드해야 하는 케이스가 많다.
    - 따라서 권한을 메서드별로 분리:
      - GET: 로그인 유저면 허용 (학생 다운로드 가능)
      - POST: Teacher/Admin만 허용
    """

    def get_permissions(self):
        """
        ✅ 메서드별 권한 분리 (정석)
        - GET: IsAuthenticated
        - POST: IsAuthenticated + IsTeacherOrAdmin
        """
        if self.request.method == "GET":
            return [IsAuthenticated()]
        return [IsAuthenticated(), IsTeacherOrAdmin()]

    def get(self, request, exam_id: int):
        exam = get_object_or_404(Exam, id=int(exam_id))
        qs = ExamAsset.objects.filter(exam=exam).order_by("asset_type")
        return Response(ExamAssetSerializer(qs, many=True).data)

    def post(self, request, exam_id: int):
        exam = get_object_or_404(Exam, id=int(exam_id))

        asset_type = request.data.get("asset_type")
        upload_file = request.FILES.get("file")

        if not asset_type or not upload_file:
            return Response({"detail": "asset_type and file are required"}, status=400)

        valid = {t for t, _ in ExamAsset.AssetType.choices}
        if asset_type not in valid:
            return Response(
                {"detail": f"asset_type must be one of {sorted(valid)}"},
                status=400,
            )

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
                "file_type": upload_file.content_type
                or mimetypes.guess_type(upload_file.name)[0],
                "file_size": upload_file.size,
            },
        )

        return Response(ExamAssetSerializer(obj).data, status=201)
