from rest_framework import serializers
from .models import Question, Answer


class QuestionSerializer(serializers.ModelSerializer):
    student_name = serializers.CharField(
        source="enrollment.student.name", read_only=True
    )

    class Meta:
        model = Question
        fields = "__all__"


class AnswerSerializer(serializers.ModelSerializer):
    class Meta:
        model = Answer
        fields = "__all__"
