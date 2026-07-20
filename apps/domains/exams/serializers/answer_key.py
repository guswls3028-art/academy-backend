from rest_framework import serializers
from apps.domains.exams.models import AnswerKey
from apps.support.omr.score_adjustment import (
    SCORE_ADJUSTMENT_KEY,
    normalize_score_adjustment_payload,
)

class AnswerKeySerializer(serializers.ModelSerializer):
    def validate_answers(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("answers must be an object")

        normalized = {}
        for k, v in value.items():
            key = str(k).strip()
            if not key:
                continue

            if key == SCORE_ADJUSTMENT_KEY:
                adjustment = normalize_score_adjustment_payload(v)
                if adjustment:
                    normalized[key] = adjustment
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

            normalized[key] = "" if v is None else str(v).strip()

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
