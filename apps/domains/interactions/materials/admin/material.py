from django.contrib import admin
from ..models import Material


@admin.register(Material)
class MaterialAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "lecture", "category", "is_public", "created_at")
    list_filter = ("lecture", "category", "is_public")
    ordering = ("-created_at",)
