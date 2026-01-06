# apps/domains/student_app/exams/serializers.py
from rest_framework import serializers


class StudentExamSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    title = serializers.CharField()
    open_at = serializers.DateTimeField(allow_null=True)
    close_at = serializers.DateTimeField(allow_null=True)
    allow_retake = serializers.BooleanField()
    max_attempts = serializers.IntegerField()
    pass_score = serializers.IntegerField()
    description = serializers.CharField(allow_null=True, required=False)
    session_id = serializers.IntegerField(allow_null=True, required=False)
