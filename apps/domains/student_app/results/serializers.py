# apps/domains/student_app/results/serializers.py
from rest_framework import serializers


class MyExamResultSerializer(serializers.Serializer):
    exam_id = serializers.IntegerField()
    attempt_id = serializers.IntegerField()
    total_score = serializers.IntegerField()
    max_score = serializers.IntegerField()
    is_pass = serializers.BooleanField()
    submitted_at = serializers.DateTimeField(allow_null=True)
    can_retake = serializers.BooleanField()


class MyExamResultItemSerializer(serializers.Serializer):
    question_id = serializers.IntegerField()
    question_number = serializers.IntegerField()
    student_answer = serializers.CharField(allow_null=True)
    correct_answer = serializers.CharField(allow_null=True)
    score = serializers.IntegerField()
    max_score = serializers.IntegerField()
    is_correct = serializers.BooleanField()
    meta = serializers.DictField(required=False)
