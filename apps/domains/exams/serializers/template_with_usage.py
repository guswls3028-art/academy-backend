from __future__ import annotations

from rest_framework import serializers


class UsedLectureSerializer(serializers.Serializer):
    lecture_id = serializers.IntegerField()
    lecture_title = serializers.CharField()
    chip_label = serializers.CharField(required=False, allow_blank=True)
    color = serializers.CharField(required=False, allow_blank=True)
    last_used_date = serializers.CharField(required=False, allow_blank=True, allow_null=True)


class TemplateWithUsageSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    title = serializers.CharField()
    subject = serializers.CharField()
    last_used_date = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    used_lectures = UsedLectureSerializer(many=True)

