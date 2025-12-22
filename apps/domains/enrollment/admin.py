from django.contrib import admin
from .models import Enrollment, SessionEnrollment


@admin.register(Enrollment)
class EnrollmentAdmin(admin.ModelAdmin):
    list_display = ("id", "student", "lecture", "status", "enrolled_at")
    list_display_links = ("id", "student")
    list_filter = ("status", "lecture")
    search_fields = ("student__name", "lecture__title")
    ordering = ("-id",)


@admin.register(SessionEnrollment)
class SessionEnrollmentAdmin(admin.ModelAdmin):
    list_display = ("id", "session", "enrollment", "created_at")
    list_display_links = ("id", "session")
    list_filter = ("session__lecture", "session")
    search_fields = ("enrollment__student__name",)
    ordering = ("-id",)
