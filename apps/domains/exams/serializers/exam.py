# apps/domains/exams/serializers/exam.py
from rest_framework import serializers
from apps.domains.exams.models import Exam


class ExamSerializer(serializers.ModelSerializer):
    
    # 과목 자동으로 입력되게.
    subject = serializers.CharField(read_only=True)

    """
    ✅ Exam 조회/수정 serializer

    프론트에서:
    - allow_retake / max_attempts / pass_score로 재시험 정책 표시/토글
    - open_at / close_at로 시험 공개/마감 UX 구현
    """

    # sessions M2M: 읽기 시 id 배열, 쓰기 시 id 배열로 설정
    session_ids = serializers.PrimaryKeyRelatedField(
        source="sessions",
        many=True,
        queryset=Exam.sessions.rel.related_model.objects.all(),
        required=False,
    )

    class Meta:
        model = Exam
        fields = [
            "id",
            "title",
            "description",
            "subject",
            "exam_type",
            "is_active",
            "status",
            # ✅ STEP 1/3
            "allow_retake",
            "max_attempts",
            "pass_score",
            "max_score",
            "display_order",
            "open_at",
            "close_at",
            "answer_visibility",
            "session_ids",

            "created_at",
            "updated_at",
        ]

    def validate(self, attrs):
        # P1-5: 시험 유효성 검증 (serializer 레벨)
        instance = self.instance
        max_attempts = attrs.get("max_attempts", getattr(instance, "max_attempts", 1) if instance else 1)
        pass_score = attrs.get("pass_score", getattr(instance, "pass_score", 0) if instance else 0)
        max_score = attrs.get("max_score", getattr(instance, "max_score", 100) if instance else 100)
        open_at = attrs.get("open_at", getattr(instance, "open_at", None) if instance else None)
        close_at = attrs.get("close_at", getattr(instance, "close_at", None) if instance else None)

        errors = {}
        if max_attempts is not None and max_attempts < 1:
            errors["max_attempts"] = "1 이상이어야 합니다."
        if pass_score is not None and max_score is not None and pass_score > max_score:
            errors["pass_score"] = f"합격 점수({pass_score})가 만점({max_score})을 초과할 수 없습니다."
        if open_at and close_at and open_at >= close_at:
            errors["close_at"] = "마감 시각이 시작 시각 이후여야 합니다."
        if errors:
            raise serializers.ValidationError(errors)

        return attrs
