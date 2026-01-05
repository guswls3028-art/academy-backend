# PATH: apps/domains/results/serializers/student_exam_result.py
from __future__ import annotations

from rest_framework import serializers
from apps.domains.results.models import Result, ResultItem


class ResultItemSerializer(serializers.ModelSerializer):
    """
    ✅ 학생 화면: 문항별 결과
    """
    class Meta:
        model = ResultItem
        fields = [
            "question_id",
            "answer",
            "is_correct",
            "score",
            "max_score",
            "source",
        ]


class StudentExamResultSerializer(serializers.ModelSerializer):
    """
    ✅ 학생 화면: 시험 결과(총점 + 문항별)
    """
    items = ResultItemSerializer(many=True, read_only=True)

    class Meta:
        model = Result
        fields = [
            "target_type",
            "target_id",
            "enrollment_id",
            "total_score",
            "max_score",
            "submitted_at",
            "items",
        ]
