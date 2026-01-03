# apps/domains/progress/filters.py
import django_filters

from .models import SessionProgress, LectureProgress, ClinicLink, RiskLog, ProgressPolicy


class ProgressPolicyFilter(django_filters.FilterSet):
    lecture = django_filters.NumberFilter(field_name="lecture_id")

    class Meta:
        model = ProgressPolicy
        fields = ["lecture"]


class SessionProgressFilter(django_filters.FilterSet):
    enrollment_id = django_filters.NumberFilter(field_name="enrollment_id")
    session = django_filters.NumberFilter(field_name="session_id")
    lecture = django_filters.NumberFilter(field_name="session__lecture_id")
    completed = django_filters.BooleanFilter(field_name="completed")

    class Meta:
        model = SessionProgress
        fields = ["enrollment_id", "session", "lecture", "completed"]


class LectureProgressFilter(django_filters.FilterSet):
    enrollment_id = django_filters.NumberFilter(field_name="enrollment_id")
    lecture = django_filters.NumberFilter(field_name="lecture_id")
    risk_level = django_filters.CharFilter(field_name="risk_level")

    class Meta:
        model = LectureProgress
        fields = ["enrollment_id", "lecture", "risk_level"]


class ClinicLinkFilter(django_filters.FilterSet):
    enrollment_id = django_filters.NumberFilter(field_name="enrollment_id")
    session = django_filters.NumberFilter(field_name="session_id")
    lecture = django_filters.NumberFilter(field_name="session__lecture_id")
    reason = django_filters.CharFilter(field_name="reason")
    is_auto = django_filters.BooleanFilter(field_name="is_auto")
    approved = django_filters.BooleanFilter(field_name="approved")

    class Meta:
        model = ClinicLink
        fields = ["enrollment_id", "session", "lecture", "reason", "is_auto", "approved"]


class RiskLogFilter(django_filters.FilterSet):
    enrollment_id = django_filters.NumberFilter(field_name="enrollment_id")
    session = django_filters.NumberFilter(field_name="session_id")
    risk_level = django_filters.CharFilter(field_name="risk_level")
    rule = django_filters.CharFilter(field_name="rule")

    class Meta:
        model = RiskLog
        fields = ["enrollment_id", "session", "risk_level", "rule"]
