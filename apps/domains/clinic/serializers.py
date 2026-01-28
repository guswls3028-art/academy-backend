# PATH: apps/domains/clinic/serializers.py

from datetime import datetime, timedelta
from rest_framework import serializers
from .models import Session, SessionParticipant, Test, Submission


class ClinicSessionSerializer(serializers.ModelSerializer):
    # (선택) 운영 페이지에서 잔여 좌석 계산하려면 participant_count 내려주면 편함
    participant_count = serializers.IntegerField(read_only=True)

    # ✅ 파생 필드: 종료 시간 (저장 X)
    end_time = serializers.SerializerMethodField()

    # ✅ [ADD] 운영 판단 필드
    available_slots = serializers.SerializerMethodField()
    is_full = serializers.SerializerMethodField()

    # ✅ [ADD] 상태/소스 요약
    status_summary = serializers.SerializerMethodField()
    source_summary = serializers.SerializerMethodField()
    has_auto_targets = serializers.SerializerMethodField()

    class Meta:
        model = Session
        fields = "__all__"

    def get_end_time(self, obj: Session):
        if not obj.start_time or not obj.duration_minutes:
            return None
        dt = datetime.combine(obj.date, obj.start_time)
        return (dt + timedelta(minutes=obj.duration_minutes)).time()

    def get_available_slots(self, obj):
        if obj.max_participants is None or obj.participant_count is None:
            return None
        return max(obj.max_participants - obj.participant_count, 0)

    def get_is_full(self, obj):
        if obj.max_participants is None or obj.participant_count is None:
            return False
        return obj.participant_count >= obj.max_participants

    def get_status_summary(self, obj):
        return {
            "booked": getattr(obj, "booked_count", 0),
            "attended": getattr(obj, "attended_count", 0),
            "no_show": getattr(obj, "no_show_count", 0),
            "cancelled": getattr(obj, "cancelled_count", 0),
        }

    def get_source_summary(self, obj):
        return {
            "auto": getattr(obj, "auto_count", 0),
            "manual": getattr(obj, "manual_count", 0),
        }

    def get_has_auto_targets(self, obj):
        return getattr(obj, "auto_count", 0) > 0


class ClinicSessionParticipantSerializer(serializers.ModelSerializer):
    student_name = serializers.CharField(source="student.name", read_only=True)
    session_date = serializers.DateField(source="session.date", read_only=True)
    session_start_time = serializers.TimeField(source="session.start_time", read_only=True)
    session_location = serializers.CharField(source="session.location", read_only=True)

    # ✅ 파생 노출
    session_duration_minutes = serializers.IntegerField(
        source="session.duration_minutes", read_only=True
    )
    session_end_time = serializers.SerializerMethodField()

    # ✅ [ADD] 변경자 이름 노출
    status_changed_by_name = serializers.CharField(
        source="status_changed_by.username",
        read_only=True,
    )

    class Meta:
        model = SessionParticipant
        fields = "__all__"

    def get_session_end_time(self, obj):
        if not obj.session.start_time or not obj.session.duration_minutes:
            return None
        dt = datetime.combine(obj.session.date, obj.session.start_time)
        return (dt + timedelta(minutes=obj.session.duration_minutes)).time()


class ClinicSessionParticipantCreateSerializer(serializers.ModelSerializer):
    """
    ✅ 예약 등록(생성) 전용
    """

    class Meta:
        model = SessionParticipant
        fields = [
            "session",
            "student",
            "status",
            "memo",
            "source",
            "enrollment_id",
            "clinic_reason",
            "participant_role",
        ]


class ClinicTestSerializer(serializers.ModelSerializer):
    class Meta:
        model = Test
        fields = "__all__"


class ClinicSubmissionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Submission
        fields = "__all__"
