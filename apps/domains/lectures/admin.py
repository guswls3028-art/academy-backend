# PATH: apps/domains/lectures/admin.py

from django.contrib import admin
from .models import Lecture, Session


def _tenant_filtered_qs(modeladmin, request, qs, tenant_path):
    """superuser는 전체, 그 외는 tenant 멤버십 기준 필터."""
    if request.user.is_superuser:
        return qs
    tenant_ids = request.user.tenant_memberships.filter(
        is_active=True, role__in=["owner", "admin", "teacher", "staff"]
    ).values_list("tenant_id", flat=True)
    return qs.filter(**{f"{tenant_path}__in": tenant_ids})


@admin.register(Lecture)
class LectureAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "tenant",
        "title",
        "name",
        "subject",
        "start_date",
        "end_date",
        "is_active",
    )
    list_display_links = ("id", "title")
    list_filter = ("tenant", "is_active", "subject")
    search_fields = ("title", "name", "subject")
    ordering = ("-id",)

    def get_queryset(self, request):
        qs = super().get_queryset(request).select_related("tenant")
        return _tenant_filtered_qs(self, request, qs, "tenant_id")


@admin.register(Session)
class SessionAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "lecture",
        "order",
        "title",
        "date",
        "exam_count",
    )
    list_display_links = ("id", "title")
    list_filter = ("lecture",)
    search_fields = ("title",)
    ordering = ("lecture", "order")

    def get_queryset(self, request):
        qs = super().get_queryset(request).select_related("lecture")
        return _tenant_filtered_qs(self, request, qs, "lecture__tenant_id")

    def exam_count(self, obj):
        return obj.exams.count()

    exam_count.short_description = "시험 수"
