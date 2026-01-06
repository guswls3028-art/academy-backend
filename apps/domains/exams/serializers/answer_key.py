from rest_framework import serializers
from apps.domains.exams.models import AnswerKey

class AnswerKeySerializer(serializers.ModelSerializer):
    def validate_answers(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("answers must be an object")

        normalized = {}
        for k, v in value.items():
            normalized[str(k)] = str(v).strip()

        return normalized

    class Meta:
        model = AnswerKey
        fields = [
            "id",
            "exam",
            "answers",
            "created_at",
            "updated_at",
        ]
