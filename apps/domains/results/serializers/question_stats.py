# apps/domains/results/serializers/question_stats.py

from rest_framework import serializers


class QuestionStatSerializer(serializers.Serializer):
    question_id = serializers.IntegerField()
    attempts = serializers.IntegerField()
    correct = serializers.IntegerField()
    accuracy = serializers.FloatField()
    avg_score = serializers.FloatField()
    max_score = serializers.FloatField()


class WrongDistributionSerializer(serializers.Serializer):
    question_id = serializers.IntegerField()
    distribution = serializers.DictField(
        child=serializers.IntegerField()
    )


class TopWrongQuestionSerializer(serializers.Serializer):
    question_id = serializers.IntegerField()
    wrong_count = serializers.IntegerField()
