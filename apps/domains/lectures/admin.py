# PATH: apps/domains/lectures/admin.py

from django.contrib import admin
from .models import Lecture, Session


# --------------------------------------------------
# Lecture
# --------------------------------------------------

@admin.register(Lecture)
class LectureAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "title",
        "name",
        "subject",
        "start_date",
        "end_date",
        "is_active",
    )
    list_display_links = ("id", "title")
    list_filter = ("is_active", "subject")
    search_fields = ("title", "name", "subject")
    ordering = ("-id",)


# --------------------------------------------------
# Session
# --------------------------------------------------

@admin.register(Session)
class SessionAdmin(admin.ModelAdmin):
    # ✅ exam 컬럼 추가 (운영자 가시성)
    list_display = ("id", "lecture", "order", "title", "date", "exam")
    list_display_links = ("id", "title")
    list_filter = ("lecture",)
    search_fields = ("title",)
    ordering = ("lecture", "order")
