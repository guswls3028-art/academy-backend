from django.contrib import admin
from .models import Dday


@admin.register(Dday)
class DdayAdmin(admin.ModelAdmin):
    list_display = ("id", "lecture", "title", "date")
    list_display_links = ("id", "title")
    list_filter = ("lecture",)
    search_fields = ("title",)
    ordering = ("date",)
