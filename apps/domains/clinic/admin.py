# PATH: apps/domains/clinic/admin.py

from django.contrib import admin
from .models import Session, SessionParticipant, Test, Submission


@admin.register(Session)
class SessionAdmin(admin.ModelAdmin):
    list_display = ("id", "date", "start_time", "location", "max_participants", "created_at")
    list_filter = ("date", "location")
    search_fields = ("location",)
    ordering = ("-date", "-start_time")


@admin.register(SessionParticipant)
class SessionParticipantAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "session",
        "student",
        "status",
        "source",
        "enrollment_id",
        "clinic_reason",
        "created_at",
    )
    list_filter = ("status", "source", "clinic_reason", "session__date")
    search_fields = ("student__name", "session__location")
    ordering = ("-created_at",)


@admin.register(Test)
class TestAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "session", "round", "date")
    list_filter = ("session", "date")
    search_fields = ("title",)
    ordering = ("-date",)


@admin.register(Submission)
class SubmissionAdmin(admin.ModelAdmin):
    list_display = ("id", "test", "student", "status", "score", "created_at")
    list_filter = ("status", "test__session__date")
    search_fields = ("student__name", "test__title")
    ordering = ("-created_at",)
