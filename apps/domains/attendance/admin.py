from django.contrib import admin
from .models import Attendance


@admin.register(Attendance)
class AttendanceAdmin(admin.ModelAdmin):
    list_display = ("id", "enrollment", "session", "status", "recorded_at")
    list_display_links = ("id", "enrollment")
    list_filter = ("status", "session__lecture")
    search_fields = ("enrollment__student__name",)
    ordering = ("-recorded_at",)
