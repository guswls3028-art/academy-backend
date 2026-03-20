# apps/domains/progress/serializers.py
from rest_framework import serializers

from .models import ProgressPolicy, SessionProgress, LectureProgress, ClinicLink, RiskLog


class ProgressPolicySerializer(serializers.ModelSerializer):
    class Meta:
        model = ProgressPolicy
        fields = "__all__"


class SessionProgressSerializer(serializers.ModelSerializer):
    # Backward-compat: expose FK _id value under original key name
    enrollment_id = serializers.IntegerField(read_only=True)

    class Meta:
        model = SessionProgress
        fields = [
            "id",
            "enrollment_id",
            "session",
            "attendance_type",
            "video_progress_rate",
            "video_completed",
            "exam_attempted",
            "exam_aggregate_score",
            "exam_passed",
            "exam_meta",
            "homework_submitted",
            "homework_passed",
            "completed",
            "completed_at",
            "calculated_at",
            "meta",
            "created_at",
            "updated_at",
        ]


class LectureProgressSerializer(serializers.ModelSerializer):
    # Backward-compat: expose FK _id value under original key name
    enrollment_id = serializers.IntegerField(read_only=True)

    class Meta:
        model = LectureProgress
        fields = [
            "id",
            "enrollment_id",
            "lecture",
            "total_sessions",
            "completed_sessions",
            "failed_sessions",
            "consecutive_failed_sessions",
            "risk_level",
            "last_session",
            "last_updated",
            "meta",
            "created_at",
            "updated_at",
        ]


class ClinicLinkSerializer(serializers.ModelSerializer):
    enrollment_id = serializers.IntegerField(read_only=True)
    session_title = serializers.CharField(source="session.title", read_only=True, default="")
    lecture_title = serializers.CharField(source="session.lecture.title", read_only=True, default="")
    student_name = serializers.SerializerMethodField()

    class Meta:
        model = ClinicLink
        fields = [
            "id",
            "enrollment_id",
            "session",
            "session_title",
            "lecture_title",
            "student_name",
            "reason",
            "is_auto",
            "approved",
            "resolved_at",
            "resolution_type",
            "resolution_evidence",
            "cycle_no",
            "memo",
            "meta",
            "created_at",
            "updated_at",
        ]

    def get_student_name(self, obj) -> str:
        try:
            return obj.enrollment.student.name
        except Exception:
            return ""


class RiskLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = RiskLog
        fields = "__all__"
