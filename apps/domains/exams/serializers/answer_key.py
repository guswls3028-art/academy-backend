from rest_framework import serializers
from apps.domains.exams.models import AnswerKey

class AnswerKeySerializer(serializers.ModelSerializer):
    class Meta:
        model = AnswerKey
        fields = [
            "id",
            "exam",
            "answers",
            "created_at",
            "updated_at",
        ]
