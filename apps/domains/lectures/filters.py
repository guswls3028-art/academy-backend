# -*- coding: utf-8 -*-

import django_filters
from .models import Enrollment, Attendance


# --------------------------------------------------
# Utils
# --------------------------------------------------

def split_multi(value: str):
    """
    콤마(,)로 구분된 문자열을 리스트로 변환 (null-safe)
    """
    if not value:
        return []
    return [v.strip() for v in value.split(",") if v.strip()]


# ==================================================
# Enrollment Filter
# ==================================================

class EnrollmentFilter(django_filters.FilterSet):
    # ----- 텍스트 검색 -----
    student_name = django_filters.CharFilter(method="filter_student_name")
    student_phone = django_filters.CharFilter(method="filter_student_phone")
    parent_phone = django_filters.CharFilter(method="filter_parent_phone")
    attendance_memo = django_filters.CharFilter(method="filter_attendance_memo")

    # ----- 다중 선택 -----
    tags = django_filters.CharFilter(method="filter_tags")  # AND 조건
    status = django_filters.CharFilter(method="filter_status")

    # ----- 숫자 -----
    lecture = django_filters.NumberFilter(field_name="lecture_id")
    student = django_filters.NumberFilter(field_name="student_id")

    def filter_student_name(self, qs, name, value):
        return qs.filter(student__name__icontains=value)

    def filter_student_phone(self, qs, name, value):
        return qs.filter(student__phone__icontains=value)

    def filter_parent_phone(self, qs, name, value):
        return qs.filter(student__parent_phone__icontains=value)

    def filter_attendance_memo(self, qs, name, value):
        return qs.filter(attendances__memo__icontains=value)

    def filter_tags(self, qs, name, value):
        """
        tags=tag1,tag2 → AND 조건
        학생은 모든 태그를 가지고 있어야 함
        """
        for tag in split_multi(value):
            qs = qs.filter(student__tags__name=tag)
        return qs.distinct()

    def filter_status(self, qs, name, value):
        return qs.filter(status__in=split_multi(value))

    class Meta:
        model = Enrollment
        fields = []


# ==================================================
# Attendance Filter
# ==================================================

class AttendanceFilter(django_filters.FilterSet):
    student_name = django_filters.CharFilter(method="filter_student_name")
    memo = django_filters.CharFilter(field_name="memo", lookup_expr="icontains")
    status = django_filters.CharFilter(method="filter_status")

    session = django_filters.NumberFilter(field_name="session_id")
    enrollment = django_filters.NumberFilter(field_name="enrollment_id")

    def filter_student_name(self, qs, name, value):
        return qs.filter(enrollment__student__name__icontains=value)

    def filter_status(self, qs, name, value):
        return qs.filter(status__in=split_multi(value))

    class Meta:
        model = Attendance
        fields = []
