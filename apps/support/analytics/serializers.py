# apps/support/analytics/serializers.py
from __future__ import annotations

from rest_framework import serializers


# ============================================================
# 시험 요약 통계 (관리자)
# ============================================================
class ExamSummarySerializer(serializers.Serializer):
    target_type = serializers.CharField()
    target_id = serializers.IntegerField()

    participant_count = serializers.IntegerField()

    avg_score = serializers.FloatField()
    min_score = serializers.FloatField()
    max_score = serializers.FloatField()

    pass_count = serializers.IntegerField()
    fail_count = serializers.IntegerField()
    pass_rate = serializers.FloatField()

    clinic_count = serializers.IntegerField()


# ============================================================
# 문항별 통계 (관리자 / 교사용)
# ============================================================
class QuestionStatSerializer(serializers.Serializer):
    question_id = serializers.IntegerField()

    attempts = serializers.IntegerField()
    correct_count = serializers.IntegerField()
    wrong_count = serializers.IntegerField()

    # ✅ answer_rate → correct_rate (의미 명확)
    correct_rate = serializers.FloatField()

    avg_score = serializers.FloatField()
    max_score = serializers.FloatField()


# ============================================================
# 오답 분포
# ============================================================
class WrongAnswerDistributionSerializer(serializers.Serializer):
    question_id = serializers.IntegerField()
    total = serializers.IntegerField()
    top = serializers.ListField(child=serializers.DictField())


# ============================================================
# 관리자 성적 리스트 (신규)
# ============================================================
class ExamResultRowSerializer(serializers.Serializer):
    enrollment_id = serializers.IntegerField()
    student_name = serializers.CharField()

    total_score = serializers.FloatField()
    max_score = serializers.FloatField()

    passed = serializers.BooleanField()
    clinic_required = serializers.BooleanField()

    submitted_at = serializers.DateTimeField(allow_null=True)
