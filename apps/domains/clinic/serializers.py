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
    session_date = serializers.SerializerMethodField()  # ✅ session이 없을 수 있으므로 SerializerMethodField 사용
    session_start_time = serializers.SerializerMethodField()
    session_location = serializers.SerializerMethodField()

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
    - 선생: student, enrollment_id 직접 지정, session 필수
    - 학생: student 생략 가능 (자동 설정), source="student_request", status="pending"
    - 학생 신청 시: session 또는 (requested_date + requested_start_time) 필수
    """

    class Meta:
        model = SessionParticipant
        fields = [
            "session",
            "requested_date",  # ✅ 학생 신청 시 날짜
            "requested_start_time",  # ✅ 학생 신청 시 시간
            "student",
            "status",
            "memo",
            "source",
            "enrollment_id",
            "clinic_reason",
            "participant_role",
        ]
        extra_kwargs = {
            "student": {"required": False},  # 학생 신청 시 생략 가능
            "session": {"required": False},  # 학생 신청 시 세션이 없을 수 있음
            "requested_date": {"required": False},
            "requested_start_time": {"required": False},
        }
    
    def validate(self, attrs):
        """session 또는 (requested_date + requested_start_time) 중 하나는 필수"""
        session = attrs.get("session")
        requested_date = attrs.get("requested_date")
        requested_start_time = attrs.get("requested_start_time")
        
        if not session and not (requested_date and requested_start_time):
            raise serializers.ValidationError(
                "session 또는 (requested_date + requested_start_time) 중 하나는 필수입니다."
            )
        
        if session and (requested_date or requested_start_time):
            raise serializers.ValidationError(
                "session과 requested_date/requested_start_time을 동시에 사용할 수 없습니다."
            )
        
        return attrs


class ClinicTestSerializer(serializers.ModelSerializer):
    class Meta:
        model = Test
        fields = "__all__"


class ClinicSubmissionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Submission
        fields = "__all__"
