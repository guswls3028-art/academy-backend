import re

import django_filters
from django.db.models import F, Q, Value
from django.db.models.functions import Replace
from rest_framework.filters import SearchFilter

from .models import Student


_PHONE_QUERY_PATTERN = re.compile(r"^[0-9\s().-]+$")


def _full_mobile_phone(value: object) -> str | None:
    raw = str(value or "").strip()
    if not raw or not _PHONE_QUERY_PATTERN.fullmatch(raw):
        return None
    digits = re.sub(r"\D", "", raw)
    return digits if len(digits) == 11 and digits.startswith("010") else None


def _normalized_phone(field_name: str):
    expression = F(field_name)
    for separator in ("-", " ", "(", ")", "."):
        expression = Replace(expression, Value(separator), Value(""))
    return expression


class StudentSearchFilter(SearchFilter):
    """Treat a full mobile number as one exact, formatting-insensitive term."""

    def filter_queryset(self, request, queryset, view):
        normalized = _full_mobile_phone(request.query_params.get(self.search_param))
        if normalized is None:
            return super().filter_queryset(request, queryset, view)
        return queryset.annotate(
            _student_phone_digits=_normalized_phone("phone"),
            _parent_phone_digits=_normalized_phone("parent_phone"),
        ).filter(
            Q(_student_phone_digits=normalized) | Q(_parent_phone_digits=normalized)
        ).distinct()


class StudentFilter(django_filters.FilterSet):
    ps_number = django_filters.CharFilter(field_name="ps_number", lookup_expr="icontains")  # ✅ NEW
    omr_code = django_filters.CharFilter(field_name="omr_code", lookup_expr="icontains")    # ✅ NEW

    name = django_filters.CharFilter(field_name="name", lookup_expr="icontains")
    gender = django_filters.CharFilter()
    grade = django_filters.NumberFilter()
    high_school = django_filters.CharFilter(lookup_expr="icontains")
    major = django_filters.CharFilter(lookup_expr="icontains")
    is_managed = django_filters.BooleanFilter()
    student_phone = django_filters.CharFilter(method="filter_exact_phone")
    parent_phone = django_filters.CharFilter(method="filter_exact_phone")
    # 고등학교 = HIGH만, 중학교 = MIDDLE만 (미입력/빈값 제외, 완전 일치)
    school_type = django_filters.ChoiceFilter(
        choices=Student.SCHOOL_TYPE_CHOICES,
        field_name="school_type",
        lookup_expr="exact",
    )

    class Meta:
        model = Student
        fields = [
            "ps_number",
            "omr_code",
            "name",
            "gender",
            "grade",
            "high_school",
            "major",
            "is_managed",
            "school_type",
            "student_phone",
            "parent_phone",
        ]

    def filter_exact_phone(self, queryset, name, value):
        normalized = _full_mobile_phone(value)
        if normalized is None:
            return queryset.none()
        field_name = "phone" if name == "student_phone" else "parent_phone"
        annotation = f"_{field_name}_filter_digits"
        return queryset.annotate(
            **{annotation: _normalized_phone(field_name)},
        ).filter(**{annotation: normalized})
