import django_filters
from .models import Student


class StudentFilter(django_filters.FilterSet):
    name = django_filters.CharFilter(field_name="name", lookup_expr="icontains")
    gender = django_filters.CharFilter()
    grade = django_filters.NumberFilter()
    high_school = django_filters.CharFilter(lookup_expr="icontains")
    major = django_filters.CharFilter(lookup_expr="icontains")
    is_managed = django_filters.BooleanFilter()

    class Meta:
        model = Student
        fields = [
            "name",
            "gender",
            "grade",
            "high_school",
            "major",
            "is_managed",
        ]
