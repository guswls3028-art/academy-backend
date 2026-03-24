# apps/domains/exams/serializers/question_explanation.py
from rest_framework import serializers

from apps.domains.exams.models.question_explanation import QuestionExplanation
from apps.infrastructure.storage.r2 import generate_presigned_get_url_storage


class QuestionExplanationSerializer(serializers.ModelSerializer):
    image_url = serializers.SerializerMethodField()

    class Meta:
        model = QuestionExplanation
        fields = [
            "id",
            "question",
            "text",
            "image_key",
            "image_url",
            "source",
            "match_confidence",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "image_url", "created_at", "updated_at"]

    def get_image_url(self, obj) -> str | None:
        if not obj.image_key:
            return None
        try:
            return generate_presigned_get_url_storage(
                key=obj.image_key, expires_in=3600,
            )
        except Exception:
            return None


class QuestionExplanationWriteSerializer(serializers.Serializer):
    """강사가 해설을 직접 입력/수정할 때 사용."""
    text = serializers.CharField(required=False, allow_blank=True, default="")
    image_key = serializers.CharField(required=False, allow_blank=True, default="")


class BulkExplanationSerializer(serializers.Serializer):
    """여러 문항의 해설을 한 번에 저장."""
    explanations = serializers.ListField(
        child=serializers.DictField(),
        help_text="[{question_id: int, text: str, image_key?: str}]",
    )
