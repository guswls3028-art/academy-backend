from __future__ import annotations

from rest_framework import serializers


class ExamQuestionInitSerializer(serializers.Serializer):
    total_questions = serializers.IntegerField(min_value=0, max_value=500)
    default_score = serializers.FloatField(required=False, min_value=0.0)

