from rest_framework import serializers
from apps.domains.exams.models import Exam


class ExamCreateSerializer(serializers.ModelSerializer):
    """
    생성 전용 serializer (Production Grade)

    정책:
    - template:
        - subject 필수
    - regular:
        - subject 입력 금지 (template에서 자동 복사)
    """

    # 🔥 핵심 수정
    subject = serializers.CharField(
        required=False,
        allow_blank=True,
    )

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
        subject = attrs.get("subject")

        # ✅ TEMPLATE
        if exam_type == Exam.ExamType.TEMPLATE:
            if not subject:
                raise serializers.ValidationError(
                    {"subject": "subject is required for template exam"}
                )

        # ✅ REGULAR
        if exam_type == Exam.ExamType.REGULAR:
            if subject:
                raise serializers.ValidationError(
                    {"subject": "regular exam must not set subject"}
                )

        return attrs
