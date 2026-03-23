# PATH: apps/domains/homework/serializers/homework_enrollment_serializer.py
from __future__ import annotations

from rest_framework import serializers


class HomeworkEnrollmentRowSerializer(serializers.Serializer):
    enrollment_id = serializers.IntegerField()
    student_name = serializers.CharField(allow_blank=True, required=False)
    is_selected = serializers.BooleanField()
    # 학생 상세 (대상자 관리 테이블용)
    profile_photo_url = serializers.URLField(allow_null=True, required=False)
    lecture_title = serializers.CharField(allow_blank=True, allow_null=True, required=False)
    lecture_color = serializers.CharField(allow_blank=True, allow_null=True, required=False)
    lecture_chip_label = serializers.CharField(allow_blank=True, allow_null=True, required=False)
    parent_phone = serializers.CharField(allow_blank=True, allow_null=True, required=False)
    student_phone = serializers.CharField(allow_blank=True, allow_null=True, required=False)
    school = serializers.CharField(allow_blank=True, allow_null=True, required=False)
    grade = serializers.IntegerField(allow_null=True, required=False)


class HomeworkEnrollmentUpdateSerializer(serializers.Serializer):
    enrollment_ids = serializers.ListField(
        child=serializers.IntegerField(),
        allow_empty=True,
        required=True,
    )
