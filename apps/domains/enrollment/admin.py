from django.contrib import admin
from .models import Enrollment, SessionEnrollment


def _tenant_filtered_qs(modeladmin, request, qs, tenant_path):
    """superuser는 전체, 그 외는 tenant 멤버십 기준 필터."""
    if request.user.is_superuser:
        return qs
    tenant_ids = request.user.tenant_memberships.filter(
        is_active=True, role__in=["owner", "admin", "teacher", "staff"]
    ).values_list("tenant_id", flat=True)
    return qs.filter(**{f"{tenant_path}__in": tenant_ids})


@admin.register(Enrollment)
class EnrollmentAdmin(admin.ModelAdmin):
    list_display = ("id", "student", "lecture", "status", "enrolled_at")
    list_display_links = ("id", "student")
    list_filter = ("status", "lecture")
    search_fields = ("student__name", "lecture__title")
    ordering = ("-id",)

    def get_queryset(self, request):
        qs = super().get_queryset(request).select_related("lecture")
        return _tenant_filtered_qs(self, request, qs, "lecture__tenant_id")


@admin.register(SessionEnrollment)
class SessionEnrollmentAdmin(admin.ModelAdmin):
    list_display = ("id", "session", "enrollment", "created_at")
    list_display_links = ("id", "session")
    list_filter = ("session__lecture", "session")
    search_fields = ("enrollment__student__name",)
    ordering = ("-id",)

    def get_queryset(self, request):
        qs = super().get_queryset(request).select_related("session__lecture")
        return _tenant_filtered_qs(self, request, qs, "session__lecture__tenant_id")
