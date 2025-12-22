import django_filters
from django.db.models import Q

from .models import Staff, WorkRecord, ExpenseRecord


class StaffFilter(django_filters.FilterSet):
    search = django_filters.CharFilter(method="filter_search")
    is_active = django_filters.BooleanFilter()
    is_manager = django_filters.BooleanFilter()
    pay_type = django_filters.CharFilter()

    class Meta:
        model = Staff
        fields = ["is_active", "is_manager", "pay_type"]

    def filter_search(self, queryset, name, value):
        return queryset.filter(
            Q(name__icontains=value) |
            Q(phone__icontains=value)
        )


class WorkRecordFilter(django_filters.FilterSet):
    date_from = django_filters.DateFilter(field_name="date", lookup_expr="gte")
    date_to = django_filters.DateFilter(field_name="date", lookup_expr="lte")

    class Meta:
        model = WorkRecord
        fields = ["staff", "work_type", "date_from", "date_to"]


class ExpenseRecordFilter(django_filters.FilterSet):
    date_from = django_filters.DateFilter(field_name="date", lookup_expr="gte")
    date_to = django_filters.DateFilter(field_name="date", lookup_expr="lte")
    status = django_filters.CharFilter()

    class Meta:
        model = ExpenseRecord
        fields = ["staff", "status", "date_from", "date_to"]
