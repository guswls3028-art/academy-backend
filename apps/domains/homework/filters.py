import django_filters

# DESIGN:
# - /homework/scores/* 엔드포인트는 유지하되
# - Score 스냅샷의 단일 진실은 homework_results.HomeworkScore 이다.
from apps.domains.homework_results.models import HomeworkScore


class HomeworkScoreFilter(django_filters.FilterSet):
    enrollment_id = django_filters.NumberFilter(field_name="enrollment_id")
    session = django_filters.NumberFilter(field_name="session_id")
    lecture = django_filters.NumberFilter(field_name="session__lecture_id")
    is_locked = django_filters.BooleanFilter(field_name="is_locked")

    class Meta:
        model = HomeworkScore
        fields = ["enrollment_id", "session", "lecture", "is_locked"]
