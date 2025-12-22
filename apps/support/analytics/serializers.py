# apps/support/analytics/serializers.py
from __future__ import annotations

from rest_framework import serializers


class ExamSummarySerializer(serializers.Serializer):
    target_type = serializers.CharField()
    target_id = serializers.IntegerField()

    participant_count = serializers.IntegerField()
    average_score = serializers.FloatField()
    max_score = serializers.FloatField()


class QuestionStatSerializer(serializers.Serializer):
    question_id = serializers.IntegerField()
    attempts = serializers.IntegerField()
    correct_count = serializers.IntegerField()
    wrong_count = serializers.IntegerField()
    answer_rate = serializers.FloatField()
    avg_score = serializers.FloatField()
    max_score = serializers.FloatField()


class WrongAnswerDistributionSerializer(serializers.Serializer):
    question_id = serializers.IntegerField()
    total = serializers.IntegerField()
    top = serializers.ListField(child=serializers.DictField())
