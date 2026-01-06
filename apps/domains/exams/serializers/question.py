from rest_framework import serializers
from apps.domains.exams.models import ExamQuestion


class QuestionSerializer(serializers.ModelSerializer):
    """
    🔧 PATCH:
    - ExamQuestion.region_meta(bbox)가 이미 모델/서비스에서 저장되는데
      serializer에서 누락되면 프론트에서 하이라이트/오답노트 영역표시 불가.
    """

    class Meta:
        model = ExamQuestion
        fields = [
            "id",
            "sheet",
            "number",
            "score",
            "image",
            "region_meta",  # ✅ 추가
            "created_at",
            "updated_at",
        ]
