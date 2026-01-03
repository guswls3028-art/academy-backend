# apps/domains/progress/serializers.py
from rest_framework import serializers

from .models import ProgressPolicy, SessionProgress, LectureProgress, ClinicLink, RiskLog


class ProgressPolicySerializer(serializers.ModelSerializer):
    class Meta:
        model = ProgressPolicy
        fields = "__all__"


class SessionProgressSerializer(serializers.ModelSerializer):
    class Meta:
        model = SessionProgress
        fields = "__all__"


class LectureProgressSerializer(serializers.ModelSerializer):
    class Meta:
        model = LectureProgress
        fields = "__all__"


class ClinicLinkSerializer(serializers.ModelSerializer):
    class Meta:
        model = ClinicLink
        fields = "__all__"


class RiskLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = RiskLog
        fields = "__all__"
