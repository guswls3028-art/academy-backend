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
            "allow_retake",
            "max_attempts",
            "pass_score",
            "max_score",
            "answer_visibility",
            "open_at",
            "close_at",
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

        max_attempts = attrs.get("max_attempts", 1)
        pass_score = attrs.get("pass_score", 0)
        max_score = attrs.get("max_score", 100)
        open_at = attrs.get("open_at")
        close_at = attrs.get("close_at")
        errors = {}
        if max_attempts is not None and max_attempts < 1:
            errors["max_attempts"] = "1 이상이어야 합니다."
        if pass_score is not None and max_score is not None and pass_score > max_score:
            errors["pass_score"] = "합격 점수는 만점을 초과할 수 없습니다."
        if open_at and close_at and open_at >= close_at:
            errors["close_at"] = "마감 시각이 시작 시각 이후여야 합니다."
        if errors:
            raise serializers.ValidationError(errors)

        return attrs
