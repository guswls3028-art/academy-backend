from django.contrib import admin
from .models import Attendance


@admin.register(Attendance)
class AttendanceAdmin(admin.ModelAdmin):
    list_display = ("id", "enrollment", "session", "status", "recorded_at")
    list_display_links = ("id", "enrollment")
    list_filter = ("status", "session__lecture")
    search_fields = ("enrollment__student__name",)
    ordering = ("-recorded_at",)

    def get_queryset(self, request):
        qs = super().get_queryset(request).select_related("session__lecture")
        if request.user.is_superuser:
            return qs
        tenant_ids = request.user.tenant_memberships.filter(
            is_active=True, role__in=["owner", "admin", "teacher", "staff"]
        ).values_list("tenant_id", flat=True)
        return qs.filter(session__lecture__tenant_id__in=tenant_ids)
