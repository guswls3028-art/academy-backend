# PATH: apps/domains/lectures/admin.py

from django.contrib import admin
from .models import Lecture, Session


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

    def exam_count(self, obj):
        return obj.exams.count()

    exam_count.short_description = "시험 수"
