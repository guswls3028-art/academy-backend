# apps/domains/results/serializers/admin_exam_result_row.py
from rest_framework import serializers


class AdminExamResultRowSerializer(serializers.Serializer):
    enrollment_id = serializers.IntegerField()
    student_name = serializers.CharField()

    # =====================================
    # 🔧 PATCH: 점수 필드 명시적 분리
    # - SessionScores / AdminExamResults 공용 계약
    # - 프론트 수정 없이 확장 가능
    # =====================================
    exam_score = serializers.FloatField()
    exam_max_score = serializers.FloatField()

    final_score = serializers.FloatField()
    passed = serializers.BooleanField()
    clinic_required = serializers.BooleanField()

    submitted_at = serializers.DateTimeField(allow_null=True)

    # ===============================
    # 🔥 Submission 연동 필드 (기존 유지)
    # ===============================
    submission_id = serializers.IntegerField(allow_null=True)
    submission_status = serializers.CharField(allow_null=True)
