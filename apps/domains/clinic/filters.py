# PATH: apps/domains/clinic/filters.py

import re

import django_filters
from .models import Session, Submission, SessionParticipant


ONSITE_PARTICIPANT_ORDERING = ("checked_in_at", "session__start_time", "id")
ONSITE_DATE_PATTERN = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


class SessionFilter(django_filters.FilterSet):
    date = django_filters.DateFilter()
    date_from = django_filters.DateFilter(field_name="date", lookup_expr="gte")
    date_to = django_filters.DateFilter(field_name="date", lookup_expr="lte")
    target_grade = django_filters.NumberFilter(field_name="target_grade")
    target_school_type = django_filters.CharFilter(field_name="target_school_type")
    section = django_filters.NumberFilter(field_name="section_id")

    class Meta:
        model = Session
        fields = ["date", "location", "target_grade", "target_school_type", "section"]


class ParticipantFilter(django_filters.FilterSet):
    session = django_filters.NumberFilter(field_name="session_id")
    student = django_filters.NumberFilter(field_name="student_id")
    status = django_filters.CharFilter(field_name="status")
    source = django_filters.CharFilter(field_name="source")
    enrollment_id = django_filters.NumberFilter(field_name="enrollment_id")
    clinic_reason = django_filters.CharFilter(field_name="clinic_reason")

    session_date = django_filters.DateFilter(field_name="session__date")
    session_date_from = django_filters.DateFilter(field_name="session__date", lookup_expr="gte")
    session_date_to = django_filters.DateFilter(field_name="session__date", lookup_expr="lte")
    onsite_date = django_filters.DateFilter(
        method="filter_onsite_date",
        help_text=(
            "현장 운영 날짜(YYYY-MM-DD). 현재 테넌트의 등원 후 미하원 참가자만 "
            "현장 정렬로 반환합니다."
        ),
    )

    def is_valid(self):
        is_valid = super().is_valid()
        if not self.is_bound or "onsite_date" not in self.data:
            return is_valid
        raw_value = self.data.get("onsite_date")
        has_exact_format = bool(
            isinstance(raw_value, str) and ONSITE_DATE_PATTERN.fullmatch(raw_value)
        )
        if not has_exact_format and "onsite_date" not in self.form.errors:
            self.form.add_error("onsite_date", "YYYY-MM-DD 형식의 날짜가 필요합니다.")
        return is_valid and has_exact_format

    def filter_onsite_date(self, queryset, name, value):
        tenant = getattr(self.request, "tenant", None)
        if tenant is None:
            return queryset.none()
        return queryset.filter(
            tenant=tenant,
            session__tenant=tenant,
            student__tenant=tenant,
            session__date=value,
            status=SessionParticipant.Status.ATTENDED,
            checked_in_at__isnull=False,
            checked_out_at__isnull=True,
        ).order_by(*ONSITE_PARTICIPANT_ORDERING)

    class Meta:
        model = SessionParticipant
        fields = [
            "session",
            "student",
            "status",
            "source",
            "enrollment_id",
            "clinic_reason",
        ]


class SubmissionFilter(django_filters.FilterSet):
    session = django_filters.NumberFilter(method="filter_session")
    test = django_filters.NumberFilter(field_name="test_id")
    student = django_filters.NumberFilter(field_name="student_id")
    status = django_filters.CharFilter(field_name="status")

    need_file = django_filters.BooleanFilter(method="filter_need_file")
    need_score = django_filters.BooleanFilter(method="filter_need_score")
    need_grade = django_filters.BooleanFilter(method="filter_need_grade")

    class Meta:
        model = Submission
        fields = ["test", "student", "status"]

    def filter_session(self, queryset, name, value):
        return queryset.filter(test__session_id=value)

    def filter_need_file(self, queryset, name, value):
        return queryset.filter(file__isnull=True) if value else queryset

    def filter_need_score(self, queryset, name, value):
        return queryset.filter(score__isnull=True) if value else queryset

    def filter_need_grade(self, queryset, name, value):
        return queryset.filter(status="pending") if value else queryset
