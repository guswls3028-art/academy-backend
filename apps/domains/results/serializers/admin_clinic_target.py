# PATH: apps/domains/results/serializers/admin_clinic_target.py
"""
역할
- Admin/Teacher용 "클리닉 대상자" 리스트 응답 계약을 고정한다.

설계 계약 (중요)
- Clinic 대상자 선정/판단은 results 도메인의 단일 진실이다.
- enrollment_id 기준(단일 진실)으로 내려준다.
- 프론트의 ClinicTarget 타입과 1:1로 맞춘다.

보류된 기능 (명시)
- reason의 세부 판정(점수/신뢰도)은 서비스에서 보수적으로 판정한다.
  (프로젝트마다 LOW_CONFIDENCE 신호가 Attempt.meta에 있을 수도, ResultFact.meta에 있을 수도 있어 방어 구현)
"""

from rest_framework import serializers


class AdminClinicTargetSerializer(serializers.Serializer):
    enrollment_id = serializers.IntegerField()
    student_name = serializers.CharField()
    session_title = serializers.CharField()

    reason = serializers.ChoiceField(choices=["score", "confidence", "missing"])
    clinic_reason = serializers.ChoiceField(
        choices=["exam", "homework", "both"],
        required=False,
        allow_null=True,
    )

    exam_score = serializers.FloatField(allow_null=True)
    cutline_score = serializers.FloatField(allow_null=True)

    # ✅ V1.1.1 remediation: ClinicLink 식별/상태 필드
    clinic_link_id = serializers.IntegerField(required=False, allow_null=True)
    cycle_no = serializers.IntegerField(required=False, default=1)
    resolution_type = serializers.CharField(required=False, allow_null=True)
    resolved_at = serializers.DateTimeField(required=False, allow_null=True)

    # ✅ V1.1.1 remediation: 시험/과제 페이지 직접 연결용
    session_id = serializers.IntegerField(required=False, allow_null=True)
    lecture_id = serializers.IntegerField(required=False, allow_null=True)
    exam_id = serializers.IntegerField(required=False, allow_null=True)

    # ✅ V1.1.1 clinic retake: 클리닉 재시도 점수 입력 지원
    source_type = serializers.CharField(required=False, allow_null=True)
    source_id = serializers.IntegerField(required=False, allow_null=True)
    source_title = serializers.CharField(required=False, allow_null=True)
    lecture_title = serializers.CharField(required=False, allow_null=True)
    lecture_color = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    lecture_chip_label = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    name_highlight_clinic_target = serializers.BooleanField(default=False)

    # ✅ 학생 프로필 필드 (ClinicTargetSelectModal 테이블 컬럼용)
    parent_phone = serializers.CharField(required=False, default="", allow_blank=True)
    student_phone = serializers.CharField(required=False, default="", allow_blank=True)
    school = serializers.CharField(required=False, default="", allow_blank=True)
    grade = serializers.IntegerField(required=False, allow_null=True)
    profile_photo_url = serializers.CharField(required=False, allow_null=True, allow_blank=True)

    max_score = serializers.FloatField(required=False, allow_null=True)
    latest_attempt_index = serializers.IntegerField(required=False, default=1)
    attempt_history = serializers.ListField(
        child=serializers.DictField(),
        required=False,
        default=list,
    )

    created_at = serializers.DateTimeField(allow_null=True)
