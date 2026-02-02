# PATH: apps/domains/submissions/serializers/submission.py
# 변경 요약:
# - OMR_SCAN은 enrollment_id 없이 제출 생성 허용 (식별은 OMR 마킹/AI가 채움)
# - file(FileField)로 업로드 받은 파일을 R2로 업로드 후 file_key 저장

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

        # ✅ 핵심 정책:
        # - exam/homework는 일반적으로 enrollment_id 필요
        # - 단, OMR_SCAN은 "식별을 OMR 마킹으로 처리"하므로 enrollment_id 없이 생성 허용
        if target_type in (Submission.TargetType.EXAM, Submission.TargetType.HOMEWORK):
            if source != Submission.Source.OMR_SCAN:
                if not attrs.get("enrollment_id"):
                    raise serializers.ValidationError({"enrollment_id": "required"})

        # 파일 기반 소스는 file 필요
        if source in (
            Submission.Source.OMR_SCAN,
            Submission.Source.HOMEWORK_IMAGE,
            Submission.Source.HOMEWORK_VIDEO,
        ):
            if not attrs.get("file"):
                raise serializers.ValidationError({"file": "file required"})

        # online은 payload 필요
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
                content_type=getattr(upload_file, "content_type", None),
            )

            submission.file_key = key
            submission.file_type = (
                getattr(upload_file, "content_type", None)
                or mimetypes.guess_type(upload_file.name)[0]
            )
            submission.file_size = getattr(upload_file, "size", None)
            submission.save(update_fields=["file_key", "file_type", "file_size"])

        return submission
