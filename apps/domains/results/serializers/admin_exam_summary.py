# apps/domains/results/serializers/admin_exam_summary.py
from rest_framework import serializers


class AdminExamSummarySerializer(serializers.Serializer):
    participant_count = serializers.IntegerField()

    avg_score = serializers.FloatField()
    min_score = serializers.FloatField()
    max_score = serializers.FloatField()

    pass_count = serializers.IntegerField()
    fail_count = serializers.IntegerField()
    pass_rate = serializers.FloatField()

    clinic_count = serializers.IntegerField()
