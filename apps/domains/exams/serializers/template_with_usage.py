from __future__ import annotations

from rest_framework import serializers


class UsedLectureSerializer(serializers.Serializer):
    lecture_id = serializers.IntegerField()
    lecture_title = serializers.CharField()


class TemplateWithUsageSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    title = serializers.CharField()
    subject = serializers.CharField()
    used_lectures = UsedLectureSerializer(many=True)

