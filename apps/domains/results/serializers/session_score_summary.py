# PATH: apps/domains/results/serializers/session_score_summary.py

from rest_framework import serializers


class SessionScoreSummarySerializer(serializers.Serializer):
    """
    세션 단위 성적 요약 (운영/통계용)

    ⚠️ 주의
    - Result / ResultFact / Progress 결과만 사용
    - attempt 교체와 무관하게 항상 일관된 값
    """

    participant_count = serializers.IntegerField()

    avg_score = serializers.FloatField()
    min_score = serializers.FloatField()
    max_score = serializers.FloatField()

    pass_rate = serializers.FloatField()
    clinic_rate = serializers.FloatField()

    attempt_stats = serializers.DictField()
