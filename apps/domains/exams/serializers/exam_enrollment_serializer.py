# PATH: apps/domains/exams/serializers/exam_enrollment_serializer.py

from __future__ import annotations

from rest_framework import serializers


class ExamEnrollmentRowSerializer(serializers.Serializer):
    """
    GET 응답 row (UI 편의를 위해 is_selected 포함)
    """
    enrollment_id = serializers.IntegerField()
    student_name = serializers.CharField(allow_blank=True)
    is_selected = serializers.BooleanField()


class ExamEnrollmentUpdateSerializer(serializers.Serializer):
    """
    PUT 요청 payload
    """
    enrollment_ids = serializers.ListField(
        child=serializers.IntegerField(),
        allow_empty=True,
        required=True,
    )
