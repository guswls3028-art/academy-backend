import django_filters
from .models import Session, Submission


class SessionFilter(django_filters.FilterSet):
    date = django_filters.DateFilter()
    date_from = django_filters.DateFilter(field_name="date", lookup_expr="gte")
    date_to = django_filters.DateFilter(field_name="date", lookup_expr="lte")

    class Meta:
        model = Session
        fields = ["date", "location"]


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
