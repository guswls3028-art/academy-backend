from rest_framework import serializers
from apps.domains.exams.models import Exam


class StudentExamListSerializer(serializers.ModelSerializer):
    """
    학생 노출용 시험 serializer
    """

    class Meta:
        model = Exam
        fields = [
            "id",
            "title",
            "description",
            "open_at",
            "close_at",
        ]
