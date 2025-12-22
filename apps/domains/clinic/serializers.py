from rest_framework import serializers
from .models import Session, SessionParticipant, Test, Submission


class ClinicSessionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Session
        fields = "__all__"


class ClinicSessionParticipantSerializer(serializers.ModelSerializer):
    class Meta:
        model = SessionParticipant
        fields = "__all__"


class ClinicSessionParticipantCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = SessionParticipant
        fields = ["student", "status", "memo"]


class ClinicTestSerializer(serializers.ModelSerializer):
    class Meta:
        model = Test
        fields = "__all__"


class ClinicSubmissionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Submission
        fields = "__all__"
