# PATH: apps/domains/homework/filters.py
import django_filters

from apps.domains.homework.models import HomeworkScore


class HomeworkScoreFilter(django_filters.FilterSet):
    enrollment_id = django_filters.NumberFilter(field_name="enrollment_id")
    session = django_filters.NumberFilter(field_name="session_id")
    lecture = django_filters.NumberFilter(field_name="session__lecture_id")
    is_locked = django_filters.BooleanFilter(field_name="is_locked")

    class Meta:
        model = HomeworkScore
        fields = ["enrollment_id", "session", "lecture", "is_locked"]
