# PATH: apps/domains/exams/serializers/exam_enrollment_serializer.py

from __future__ import annotations

from rest_framework import serializers


class ExamEnrollmentRowSerializer(serializers.Serializer):
    """
    GET 응답 row (UI 편의를 위해 is_selected 포함)
    차시 수강생 등록 모달과 동일한 표시를 위해 profile_photo_url, 강의 딱지 필드 추가.
    """
    enrollment_id = serializers.IntegerField()
    student_name = serializers.CharField(allow_blank=True)
    is_selected = serializers.BooleanField()
    profile_photo_url = serializers.URLField(allow_null=True, required=False)
    lecture_title = serializers.CharField(allow_blank=True, required=False)
    lecture_color = serializers.CharField(allow_blank=True, required=False)
    lecture_chip_label = serializers.CharField(allow_blank=True, allow_null=True, required=False)


class ExamEnrollmentUpdateSerializer(serializers.Serializer):
    """
    PUT 요청 payload
    """
    enrollment_ids = serializers.ListField(
        child=serializers.IntegerField(),
        allow_empty=True,
        required=True,
    )
