# apps/domains/submissions/serializers/submission.py
from rest_framework import serializers
from apps.domains.submissions.models import Submission


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

        # enrollment_id는 grading에 필요하므로 강제(시험/숙제 공통)
        if target_type in (Submission.TargetType.EXAM, Submission.TargetType.HOMEWORK):
            if not attrs.get("enrollment_id"):
                raise serializers.ValidationError({"enrollment_id": "enrollment_id is required"})

        if source in (
            Submission.Source.OMR_SCAN,
            Submission.Source.HOMEWORK_IMAGE,
            Submission.Source.HOMEWORK_VIDEO,
        ):
            if not attrs.get("file"):
                raise serializers.ValidationError({"file": "해당 source는 file 업로드가 필요합니다."})

        if source == Submission.Source.ONLINE:
            if not attrs.get("payload"):
                raise serializers.ValidationError({"payload": "온라인 제출은 payload가 필요합니다."})

        return attrs
