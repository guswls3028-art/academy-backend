# PATH: apps/domains/results/serializers/admin_clinic_target.py
"""
역할
- Admin/Teacher용 "클리닉 대상자" 리스트 응답 계약을 고정한다.

설계 계약 (중요)
- Clinic 대상자 선정/판단은 results 도메인의 단일 진실이다.
- enrollment_id 기준(단일 진실)으로 내려준다.
- 프론트의 ClinicTarget 타입과 1:1로 맞춘다.

신뢰도 reason은 Attempt.meta와 ResultFact.meta의 현재 호환 신호를 서비스가
보수적으로 합성한다.
"""

from rest_framework import serializers


class LinkedClinicBookingSerializer(serializers.Serializer):
    plan_item_id = serializers.IntegerField()
    participant_id = serializers.IntegerField()
    session_id = serializers.IntegerField()
    session_date = serializers.DateField()
    session_start_time = serializers.TimeField()
    session_end_time = serializers.TimeField()
    location = serializers.CharField()
    participant_status = serializers.ChoiceField(
        choices=["pending", "booked", "attended", "no_show", "cancelled", "rejected"]
    )
    preferred_start_time = serializers.TimeField(allow_null=True)
    preferred_end_time = serializers.TimeField(allow_null=True)
    student_request_memo = serializers.CharField(allow_blank=True)
    staff_memo = serializers.CharField(allow_blank=True)
    linked_at = serializers.DateTimeField()
    linked_by_id = serializers.IntegerField(allow_null=True)
    linkage_source = serializers.ChoiceField(choices=["participant_plan"])


class AdminClinicTargetSerializer(serializers.Serializer):
    enrollment_id = serializers.IntegerField()
    student_id = serializers.IntegerField(required=False, allow_null=True)
    student_name = serializers.CharField()
    session_title = serializers.CharField()

    reason = serializers.ChoiceField(choices=["score", "confidence", "missing"])
    clinic_reason = serializers.CharField(
        required=False,
        allow_null=True,
        help_text="Assessment source category: exam, homework, or both.",
    )

    exam_score = serializers.FloatField(allow_null=True)
    cutline_score = serializers.FloatField(allow_null=True)
    homework_score = serializers.FloatField(required=False, allow_null=True)
    homework_cutline = serializers.FloatField(required=False, allow_null=True)
    homework_cutline_mode = serializers.ChoiceField(
        choices=["PERCENT", "COUNT"],
        required=False,
        allow_null=True,
    )
    homework_cutline_value = serializers.FloatField(required=False, allow_null=True)
    homework_round_unit_percent = serializers.IntegerField(
        required=False,
        allow_null=True,
    )
    meta_status = serializers.CharField(required=False, allow_null=True)

    # ✅ V1.1.1 remediation: ClinicLink 식별/상태 필드
    clinic_link_id = serializers.IntegerField(required=False, allow_null=True)
    cycle_no = serializers.IntegerField(required=False, default=1)
    resolution_type = serializers.CharField(required=False, allow_null=True)
    resolved_at = serializers.DateTimeField(required=False, allow_null=True)
    resolution_evidence = serializers.JSONField(required=False, allow_null=True)
    resolution_history = serializers.ListField(
        child=serializers.DictField(),
        required=False,
        default=list,
    )
    linked_bookings = LinkedClinicBookingSerializer(
        many=True,
        required=False,
        default=list,
    )

    # ✅ V1.1.1 remediation: 시험/과제 페이지 직접 연결용
    session_id = serializers.IntegerField(required=False, allow_null=True)
    lecture_id = serializers.IntegerField(required=False, allow_null=True)
    exam_id = serializers.IntegerField(required=False, allow_null=True)

    # ✅ V1.1.1 clinic retake: 클리닉 재시도 점수 입력 지원
    source_type = serializers.CharField(required=False, allow_null=True)
    source_id = serializers.IntegerField(required=False, allow_null=True)
    source_title = serializers.CharField(required=False, allow_null=True)
    source_scope = serializers.CharField(required=False, allow_null=True)
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
