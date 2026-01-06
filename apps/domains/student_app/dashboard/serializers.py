# apps/domains/student_app/dashboard/serializers.py
from rest_framework import serializers


class DashboardNoticeSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    title = serializers.CharField()
    created_at = serializers.DateTimeField(allow_null=True, required=False)


class DashboardSessionSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    title = serializers.CharField()
    date = serializers.DateField(allow_null=True, required=False)
    status = serializers.CharField(allow_null=True, required=False)


class StudentDashboardSerializer(serializers.Serializer):
    notices = DashboardNoticeSerializer(many=True)
    today_sessions = DashboardSessionSerializer(many=True)
    badges = serializers.DictField(required=False)
