# apps/domains/results/serializers/admin_exam_result_row.py
from rest_framework import serializers


class AdminExamResultRowSerializer(serializers.Serializer):
    enrollment_id = serializers.IntegerField()
    student_name = serializers.CharField()

    total_score = serializers.FloatField()
    max_score = serializers.FloatField()

    passed = serializers.BooleanField()
    clinic_required = serializers.BooleanField()

    submitted_at = serializers.DateTimeField(allow_null=True)
