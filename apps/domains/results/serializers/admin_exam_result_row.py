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
    passed = serializers.BooleanField(allow_null=True)
    clinic_required = serializers.BooleanField()

    submitted_at = serializers.DateTimeField(allow_null=True)

    # ✅ 클리닉 대상 하이라이트
    name_highlight_clinic_target = serializers.BooleanField(default=False)

    # ✅ 학생 SSOT 표시용: 아바타 + 강의 딱지
    profile_photo_url = serializers.CharField(allow_null=True, allow_blank=True, required=False, default=None)
    lecture_title = serializers.CharField(allow_null=True, allow_blank=True, required=False, default=None)
    lecture_color = serializers.CharField(allow_null=True, allow_blank=True, required=False, default=None)
    lecture_chip_label = serializers.CharField(allow_null=True, allow_blank=True, required=False, default=None)

    # ===============================
    # 석차 정보
    # ===============================
    rank = serializers.IntegerField(allow_null=True, required=False, default=None)
    percentile = serializers.FloatField(allow_null=True, required=False, default=None)
    cohort_size = serializers.IntegerField(allow_null=True, required=False, default=None)
    cohort_avg = serializers.FloatField(allow_null=True, required=False, default=None)

    # ===============================
    # 🔥 Submission 연동 필드 (기존 유지)
    # ===============================
    submission_id = serializers.IntegerField(allow_null=True)
    submission_status = serializers.CharField(allow_null=True)
