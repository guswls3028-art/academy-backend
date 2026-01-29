from rest_framework import serializers
from apps.domains.exams.models import Exam


class ExamCreateSerializer(serializers.ModelSerializer):
    """
    생성 전용 serializer
    - ViewSet.perform_create의 계약을 serializer 레벨로 명시
    """

    class Meta:
        model = Exam
        fields = [
            "title",
            "description",
            "subject",
            "exam_type",
        ]

    def validate_exam_type(self, value):
        if value not in {Exam.ExamType.TEMPLATE, Exam.ExamType.REGULAR}:
            raise serializers.ValidationError("invalid exam_type")
        return value

    def validate(self, attrs):
        exam_type = attrs.get("exam_type")

        if exam_type == Exam.ExamType.TEMPLATE:
            if not attrs.get("subject"):
                raise serializers.ValidationError(
                    {"subject": "subject is required for template exam"}
                )

        return attrs
