# PATH: apps/domains/clinic/serializers.py

from rest_framework import serializers
from .models import Session, SessionParticipant, Test, Submission


class ClinicSessionSerializer(serializers.ModelSerializer):
    # (선택) 운영 페이지에서 잔여 좌석 계산하려면 participant_count 내려주면 편함
    participant_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Session
        fields = "__all__"


class ClinicSessionParticipantSerializer(serializers.ModelSerializer):
    student_name = serializers.CharField(source="student.name", read_only=True)
    session_date = serializers.DateField(source="session.date", read_only=True)
    session_start_time = serializers.TimeField(source="session.start_time", read_only=True)
    session_location = serializers.CharField(source="session.location", read_only=True)

    class Meta:
        model = SessionParticipant
        fields = "__all__"


class ClinicSessionParticipantCreateSerializer(serializers.ModelSerializer):
    """
    ✅ 예약 등록(생성) 전용
    - 프론트: session + student만 넣어도 생성 가능
    - results 연계용: enrollment_id, clinic_reason(optional)
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
        ]


class ClinicTestSerializer(serializers.ModelSerializer):
    class Meta:
        model = Test
        fields = "__all__"


class ClinicSubmissionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Submission
        fields = "__all__"
