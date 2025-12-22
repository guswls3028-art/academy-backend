from rest_framework import serializers
from apps.domains.exams.models import Exam

class ExamSerializer(serializers.ModelSerializer):
    class Meta:
        model = Exam
        fields = [
            "id",
            "title",
            "description",
            "subject",
            "exam_type",
            "is_active",
            "created_at",
            "updated_at",
        ]
