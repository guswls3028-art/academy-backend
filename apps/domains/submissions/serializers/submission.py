# PATH: apps/domains/submissions/serializers/submission.py
# 변경 요약:
# - file(FileField) 제거
# - 업로드된 파일을 R2로 즉시 업로드
# - file_key/file_type/file_size 저장

from __future__ import annotations

import mimetypes
import uuid

from rest_framework import serializers

from apps.domains.submissions.models import Submission

# ✅ API 서버 전용 R2 업로드
from apps.infrastructure.storage.r2 import upload_fileobj_to_r2


class SubmissionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Submission
        fields = "__all__"
        read_only_fields = (
            "id",
            "user",
            "status",
            "error_message",
            "meta",
            "created_at",
            "updated_at",
        )


class SubmissionCreateSerializer(serializers.ModelSerializer):
    file = serializers.FileField(required=False)

    class Meta:
        model = Submission
        fields = (
            "enrollment_id",
            "target_type",
            "target_id",
            "source",
            "file",
            "payload",
        )

    def validate(self, attrs):
        source = attrs.get("source")
        target_type = attrs.get("target_type")

        if target_type in (Submission.TargetType.EXAM, Submission.TargetType.HOMEWORK):
            if not attrs.get("enrollment_id"):
                raise serializers.ValidationError({"enrollment_id": "required"})

        if source in (
            Submission.Source.OMR_SCAN,
            Submission.Source.HOMEWORK_IMAGE,
            Submission.Source.HOMEWORK_VIDEO,
        ):
            if not attrs.get("file"):
                raise serializers.ValidationError({"file": "file required"})

        if source == Submission.Source.ONLINE and not attrs.get("payload"):
            raise serializers.ValidationError({"payload": "required"})

        return attrs

    def create(self, validated_data):
        upload_file = validated_data.pop("file", None)

        submission = Submission.objects.create(**validated_data)

        if upload_file:
            ext = upload_file.name.split(".")[-1]
            key = f"submissions/{submission.id}/{uuid.uuid4().hex}.{ext}"

            upload_fileobj_to_r2(
                fileobj=upload_file,
                key=key,
                content_type=upload_file.content_type,
            )

            submission.file_key = key
            submission.file_type = upload_file.content_type or mimetypes.guess_type(upload_file.name)[0]
            submission.file_size = upload_file.size
            submission.save(update_fields=["file_key", "file_type", "file_size"])

        return submission
