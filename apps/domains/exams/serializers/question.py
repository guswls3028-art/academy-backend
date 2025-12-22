from rest_framework import serializers
from apps.domains.exams.models import ExamQuestion

class QuestionSerializer(serializers.ModelSerializer):
    class Meta:
        model = ExamQuestion
        fields = [
            "id",
            "sheet",
            "number",
            "score",
            "image",
            "created_at",
            "updated_at",
        ]
