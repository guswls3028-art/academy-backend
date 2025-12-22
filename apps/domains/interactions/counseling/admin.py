from django.contrib import admin
from .models import Counseling


@admin.register(Counseling)
class CounselingAdmin(admin.ModelAdmin):
    list_display = ("id", "enrollment", "created_at")
    list_display_links = ("id", "enrollment")
    search_fields = ("enrollment__student__name",)
    ordering = ("-created_at",)
