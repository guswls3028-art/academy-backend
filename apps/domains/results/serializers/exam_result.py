# PATH: apps/domains/results/serializers/exam_result.py
from __future__ import annotations

from rest_framework import serializers

from apps.domains.results.models.exam_result import ExamResult


class ManualGradeItemSerializer(serializers.Serializer):
    exam_question_id = serializers.IntegerField()
    score = serializers.FloatField(required=False)
    is_correct = serializers.BooleanField(required=False)
    note = serializers.CharField(required=False, allow_blank=True)


class ManualGradeSerializer(serializers.Serializer):
    """
    Keep stable import name for views.
    Payload shape can evolve without breaking callers.
    """
    identifier = serializers.CharField(required=False, allow_blank=True)
    answers = serializers.ListField(child=serializers.DictField(), required=False)
    grades = serializers.ListField(child=ManualGradeItemSerializer(), required=False)
    note = serializers.CharField(required=False, allow_blank=True)
    overrides = serializers.ListField(child=ManualGradeItemSerializer(), required=False)


class ExamResultSerializer(serializers.ModelSerializer):
    class Meta:
        model = ExamResult
        fields = "__all__"
