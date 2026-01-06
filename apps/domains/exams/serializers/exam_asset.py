# apps/domains/exams/serializers/exam_asset.py
from __future__ import annotations

from rest_framework import serializers

from apps.domains.exams.models import ExamAsset
from apps.infrastructure.storage.r2 import generate_presigned_get_url


class ExamAssetSerializer(serializers.ModelSerializer):
    """
    ✅ ExamAsset 응답 serializer

    download_url:
    - R2 presigned GET URL
    - expires_in은 짧게 (보통 1시간) 권장
    """

    download_url = serializers.SerializerMethodField()

    class Meta:
        model = ExamAsset
        fields = [
            "id",
            "exam",
            "asset_type",
            "file_key",
            "file_type",
            "file_size",
            "download_url",
            "created_at",
            "updated_at",
        ]
        # 업로드는 View에서 처리하고 DB 필드는 서버가 확정하는 방식(정석)
        read_only_fields = ["file_key", "file_type", "file_size", "download_url"]

    def get_download_url(self, obj: ExamAsset) -> str:
        return generate_presigned_get_url(key=obj.file_key, expires_in=60 * 60)
