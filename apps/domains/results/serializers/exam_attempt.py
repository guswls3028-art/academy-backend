# apps/domains/results/serializers/exam_attempt.py (신규)

from rest_framework import serializers
from apps.domains.results.models import ExamAttempt


class ExamAttemptSerializer(serializers.ModelSerializer):
    class Meta:
        model = ExamAttempt
        # Internal attribution boundary; preserve the existing public schema.
        exclude = ("current_fact_after_id",)
