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
            "max_score",
            "display_order",
            "open_at",
            "close_at",
            "answer_visibility",
        ]

    def validate(self, attrs):
        exam: Exam = self.instance

        # 상태 전이 제한: CLOSED → OPEN (채점 결과 있으면 불가)
        new_status = attrs.get("status")
        if new_status and exam.status == Exam.Status.CLOSED:
            if new_status == Exam.Status.OPEN:
                from apps.domains.results.models import ExamResult
                if ExamResult.objects.filter(exam=exam).exists():
                    raise serializers.ValidationError(
                        {"status": "채점 결과가 있는 마감 시험은 다시 열 수 없습니다."}
                    )

        # P1-5: 시험 유효성 검증
        max_attempts = attrs.get("max_attempts", exam.max_attempts)
        pass_score = attrs.get("pass_score", exam.pass_score)
        max_score = attrs.get("max_score", exam.max_score)
        open_at = attrs.get("open_at", exam.open_at)
        close_at = attrs.get("close_at", exam.close_at)

        errors = {}
        if max_attempts is not None and max_attempts < 1:
            errors["max_attempts"] = "1 이상이어야 합니다."
        if pass_score is not None and max_score is not None and pass_score > max_score:
            errors["pass_score"] = f"합격 점수({pass_score})가 만점({max_score})을 초과할 수 없습니다."
        if open_at and close_at and open_at >= close_at:
            errors["close_at"] = "마감 시각이 시작 시각 이후여야 합니다."
        if errors:
            raise serializers.ValidationError(errors)

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
