# apps/domains/student_app/sessions/serializers.py
from rest_framework import serializers


class StudentSessionSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    title = serializers.CharField()
    date = serializers.DateField(allow_null=True, required=False)
    status = serializers.CharField(allow_null=True, required=False)
    exam_ids = serializers.ListField(
        child=serializers.IntegerField(),
        required=False,
        allow_null=True,
    )
