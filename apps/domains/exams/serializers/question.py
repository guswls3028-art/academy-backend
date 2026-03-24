from rest_framework import serializers
from apps.domains.exams.models import ExamQuestion


class QuestionSerializer(serializers.ModelSerializer):
    """
    🔧 PATCH:
    - ExamQuestion.region_meta(bbox)가 이미 모델/서비스에서 저장되는데
      serializer에서 누락되면 프론트에서 하이라이트/오답노트 영역표시 불가.
    """
    explanation_text = serializers.SerializerMethodField()
    explanation_source = serializers.SerializerMethodField()

    class Meta:
        model = ExamQuestion
        fields = [
            "id",
            "sheet",
            "number",
            "score",
            "image",
            "region_meta",  # ✅ 추가
            "explanation_text",
            "explanation_source",
            "created_at",
            "updated_at",
        ]

    def get_explanation_text(self, obj) -> str:
        try:
            return obj.explanation.text if hasattr(obj, "explanation") else ""
        except Exception:
            return ""

    def get_explanation_source(self, obj) -> str | None:
        try:
            return obj.explanation.source if hasattr(obj, "explanation") else None
        except Exception:
            return None
