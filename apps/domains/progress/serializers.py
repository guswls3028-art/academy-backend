# apps/domains/progress/serializers.py
from rest_framework import serializers

from .models import ProgressPolicy, SessionProgress, LectureProgress, ClinicLink, RiskLog


class TenantScopedRelatedFieldsMixin:
    tenant_related_fields = {}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = self.context.get("request")
        tenant = getattr(request, "tenant", None)

        for field_name, (queryset_lookup, _tenant_id_path) in self.tenant_related_fields.items():
            field = self.fields.get(field_name)
            queryset = getattr(field, "queryset", None)
            if queryset is None:
                continue
            field.queryset = (
                queryset.filter(**{queryset_lookup: tenant})
                if tenant
                else queryset.none()
            )

    def _related_value(self, attrs, field_name):
        if field_name in attrs:
            return attrs[field_name]
        if self.instance is not None:
            return getattr(self.instance, field_name, None)
        return None

    @staticmethod
    def _nested_attribute(value, path):
        for part in path.split("."):
            value = getattr(value, part, None)
            if value is None:
                break
        return value

    def validate(self, attrs):
        attrs = super().validate(attrs)
        request = self.context.get("request")
        tenant = getattr(request, "tenant", None)
        if not tenant:
            raise serializers.ValidationError({"detail": "학원 정보가 필요합니다."})

        errors = {}
        for field_name, (_queryset_lookup, tenant_id_path) in self.tenant_related_fields.items():
            related = self._related_value(attrs, field_name)
            if related is None:
                continue
            if self._nested_attribute(related, tenant_id_path) != tenant.id:
                errors[field_name] = "다른 학원의 항목은 사용할 수 없습니다."

        if errors:
            raise serializers.ValidationError(errors)
        return attrs


class ProgressPolicySerializer(TenantScopedRelatedFieldsMixin, serializers.ModelSerializer):
    tenant_related_fields = {
        "lecture": ("tenant", "tenant_id"),
    }

    class Meta:
        model = ProgressPolicy
        fields = "__all__"


class SessionProgressSerializer(TenantScopedRelatedFieldsMixin, serializers.ModelSerializer):
    tenant_related_fields = {
        "enrollment": ("tenant", "tenant_id"),
        "session": ("lecture__tenant", "lecture.tenant_id"),
    }

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

    def validate(self, attrs):
        attrs = super().validate(attrs)
        enrollment = self._related_value(attrs, "enrollment")
        session = self._related_value(attrs, "session")
        if enrollment and session and enrollment.lecture_id != session.lecture_id:
            raise serializers.ValidationError(
                {"session": "수강 등록과 같은 강의의 차시만 사용할 수 있습니다."}
            )
        return attrs


class LectureProgressSerializer(TenantScopedRelatedFieldsMixin, serializers.ModelSerializer):
    tenant_related_fields = {
        "enrollment": ("tenant", "tenant_id"),
        "lecture": ("tenant", "tenant_id"),
        "last_session": ("lecture__tenant", "lecture.tenant_id"),
    }

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

    def validate(self, attrs):
        attrs = super().validate(attrs)
        enrollment = self._related_value(attrs, "enrollment")
        lecture = self._related_value(attrs, "lecture")
        last_session = self._related_value(attrs, "last_session")
        errors = {}
        if enrollment and lecture and enrollment.lecture_id != lecture.id:
            errors["lecture"] = "수강 등록과 같은 강의만 사용할 수 있습니다."
        if last_session and lecture and last_session.lecture_id != lecture.id:
            errors["last_session"] = "진도 강의에 속한 차시만 사용할 수 있습니다."
        if errors:
            raise serializers.ValidationError(errors)
        return attrs


class ClinicLinkSerializer(TenantScopedRelatedFieldsMixin, serializers.ModelSerializer):
    tenant_related_fields = {
        "enrollment": ("tenant", "tenant_id"),
        "session": ("lecture__tenant", "lecture.tenant_id"),
    }

    enrollment_id = serializers.IntegerField(read_only=True)
    session_title = serializers.SerializerMethodField()
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

    def get_session_title(self, obj) -> str:
        session = getattr(obj, "session", None)
        if not session:
            return ""
        return (getattr(session, "title", "") or getattr(session, "display_label", "") or "")

    def validate(self, attrs):
        attrs = super().validate(attrs)
        enrollment = self._related_value(attrs, "enrollment")
        session = self._related_value(attrs, "session")
        if enrollment and session and enrollment.lecture_id != session.lecture_id:
            raise serializers.ValidationError(
                {"session": "수강 등록과 같은 강의의 차시만 사용할 수 있습니다."}
            )
        return attrs


class RiskLogSerializer(TenantScopedRelatedFieldsMixin, serializers.ModelSerializer):
    tenant_related_fields = {
        "enrollment": ("tenant", "tenant_id"),
        "session": ("lecture__tenant", "lecture.tenant_id"),
    }

    class Meta:
        model = RiskLog
        fields = "__all__"

    def validate(self, attrs):
        attrs = super().validate(attrs)
        enrollment = self._related_value(attrs, "enrollment")
        session = self._related_value(attrs, "session")
        if enrollment and session and enrollment.lecture_id != session.lecture_id:
            raise serializers.ValidationError(
                {"session": "수강 등록과 같은 강의의 차시만 사용할 수 있습니다."}
            )
        return attrs
