# apps/domains/results/serializers/session_exams_summary.py
from rest_framework import serializers


class SessionExamRowSerializer(serializers.Serializer):
    exam_id = serializers.IntegerField()
    title = serializers.CharField(allow_blank=True)
    pass_score = serializers.FloatField()

    participant_count = serializers.IntegerField()
    avg_score = serializers.FloatField()
    min_score = serializers.FloatField()
    max_score = serializers.FloatField()

    pass_count = serializers.IntegerField()
    fail_count = serializers.IntegerField()
    pass_rate = serializers.FloatField()


class SessionExamsSummarySerializer(serializers.Serializer):
    session_id = serializers.IntegerField()

    participant_count = serializers.IntegerField()
    pass_rate = serializers.FloatField()
    clinic_rate = serializers.FloatField()

    strategy = serializers.CharField()
    pass_source = serializers.CharField()

    exams = SessionExamRowSerializer(many=True)
