from rest_framework import serializers
from apps.domains.exams.models import Exam


class ExamUpdateSerializer(serializers.ModelSerializer):
    """
    수정 전용 serializer
    - exam_type, subject 직접 변경 ❌
    - template_exam_id: regular 시험에서 시험 설정으로 템플릿 지정 가능 (한 번만)
    """

    class Meta:
        model = Exam
        fields = [
            "title",
            "description",
            "is_active",
            "status",
            "template_exam_id",
            "subject",
            "allow_retake",
            "max_attempts",
            "pass_score",
            "open_at",
            "close_at",
        ]

    def validate(self, attrs):
        exam: Exam = self.instance

        # 시험 상태 전이 제한
        new_status = attrs.get("status")
        if new_status and exam.status == Exam.Status.CLOSED:
            if new_status == Exam.Status.DRAFT:
                raise serializers.ValidationError(
                    {"status": "마감된 시험은 초안으로 되돌릴 수 없습니다."}
                )
            if new_status == Exam.Status.OPEN:
                from apps.domains.results.models import ExamResult
                if ExamResult.objects.filter(exam=exam).exists():
                    raise serializers.ValidationError(
                        {"status": "채점 결과가 있는 마감 시험은 다시 열 수 없습니다."}
                    )

        if exam.exam_type == Exam.ExamType.TEMPLATE:
            return attrs

        tid = attrs.get("template_exam_id")
        if tid is not None:
            try:
                t = Exam.objects.get(id=int(tid))
            except (TypeError, ValueError, Exam.DoesNotExist):
                raise serializers.ValidationError({"template_exam_id": "invalid"})
            if t.exam_type != Exam.ExamType.TEMPLATE:
                raise serializers.ValidationError({"template_exam_id": "must be template exam"})
            attrs["template_exam"] = t
            attrs["subject"] = t.subject

        return attrs
