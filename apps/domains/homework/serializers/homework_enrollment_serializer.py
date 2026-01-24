# PATH: apps/domains/homework/serializers/homework_enrollment_serializer.py
from __future__ import annotations

from rest_framework import serializers


class HomeworkEnrollmentRowSerializer(serializers.Serializer):
    enrollment_id = serializers.IntegerField()
    student_name = serializers.CharField(allow_blank=True, required=False)
    is_selected = serializers.BooleanField()


class HomeworkEnrollmentUpdateSerializer(serializers.Serializer):
    enrollment_ids = serializers.ListField(
        child=serializers.IntegerField(),
        allow_empty=True,
        required=True,
    )
