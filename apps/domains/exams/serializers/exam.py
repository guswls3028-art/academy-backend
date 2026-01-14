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

    class Meta:
        model = Exam
        fields = [
            "id",
            "title",
            "description",
            "subject",
            "exam_type",
            "is_active",
            # ✅ STEP 1/3
            "allow_retake",
            "max_attempts",
            "pass_score",
            "open_at",
            "close_at",
            
            "created_at",
            "updated_at",
        ]
