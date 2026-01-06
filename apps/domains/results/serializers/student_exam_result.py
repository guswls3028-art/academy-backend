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
    ✅ 학생 화면: 시험 결과(총점 + 문항별) + 재시험 버튼 판단값

    설계:
    - Result 모델 자체는 '스냅샷'이므로
      allow_retake/max_attempts/can_retake는 Exam 정책 + Attempt 상태로 계산해서 내려준다.
    - 이 값들은 "응답 필드"이지 Result DB 필드가 아니다.
      → View에서 계산 후 data에 주입하는 방식이 가장 단순/명확.
    """

    items = ResultItemSerializer(many=True, read_only=True)

    # ✅ STEP 2: 프론트 재시험 버튼 판단용 (응답 전용 필드)
    attempt_id = serializers.IntegerField(allow_null=True, required=False, read_only=True)
    can_retake = serializers.BooleanField(required=False, read_only=True)
    max_attempts = serializers.IntegerField(required=False, read_only=True)
    allow_retake = serializers.BooleanField(required=False, read_only=True)

    class Meta:
        model = Result
        fields = [
            "target_type",
            "target_id",
            "enrollment_id",

            # ✅ STEP 2
            "attempt_id",
            "total_score",
            "max_score",
            "submitted_at",
            "items",
            "allow_retake",
            "max_attempts",
            "can_retake",
        ]
