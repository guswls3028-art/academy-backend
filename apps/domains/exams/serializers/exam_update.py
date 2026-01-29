from rest_framework import serializers
from apps.domains.exams.models import Exam


class ExamUpdateSerializer(serializers.ModelSerializer):
    """
    수정 전용 serializer

    핵심 봉인:
    - exam_type 변경 ❌
    - template_exam 변경 ❌
    - subject 변경 ❌
    """

    class Meta:
        model = Exam
        fields = [
            "title",
            "description",
            "is_active",
            "allow_retake",
            "max_attempts",
            "pass_score",
            "open_at",
            "close_at",
        ]

    def validate(self, attrs):
        exam: Exam = self.instance

        if exam.exam_type == Exam.ExamType.TEMPLATE:
            # 템플릿은 구조/정책 정의까지만 허용
            # (의미상 open/close가 있어도, 실제 동작은 regular에서만)
            return attrs

        return attrs
