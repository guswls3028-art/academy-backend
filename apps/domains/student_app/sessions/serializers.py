# apps/domains/student_app/sessions/serializers.py
from rest_framework import serializers


class StudentSessionSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    title = serializers.CharField()
    order = serializers.IntegerField(required=False, allow_null=True)
    session_type = serializers.CharField(required=False, allow_null=True)
    regular_order = serializers.IntegerField(required=False, allow_null=True)
    display_label = serializers.CharField(required=False, allow_null=True)
    date = serializers.DateField(allow_null=True, required=False)
    status = serializers.CharField(allow_null=True, required=False)
    exam_ids = serializers.ListField(
        child=serializers.IntegerField(),
        required=False,
        allow_null=True,
    )
    type = serializers.CharField(default="session", required=False)
    start_time = serializers.TimeField(allow_null=True, required=False)
