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
