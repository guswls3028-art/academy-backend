from rest_framework import serializers
from apps.domains.exams.models import AnswerKey

class AnswerKeySerializer(serializers.ModelSerializer):
    def validate_answers(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("answers must be an object")

        normalized = {}
        for k, v in value.items():
            key = str(k).strip()
            if not key:
                continue

            if isinstance(v, (list, tuple, set)):
                candidates = [
                    str(item).strip()
                    for item in v
                    if str(item).strip()
                ]
                normalized[key] = candidates
                continue

            if isinstance(v, dict):
                raise serializers.ValidationError(
                    {"answers": "answer values must be strings or arrays"}
                )

            normalized[key] = str(v or "").strip()

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
