from django.contrib import admin
from ..models import MaterialAccess


@admin.register(MaterialAccess)
class MaterialAccessAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "material",
        "student",
        "enrollment",
        "session",
        "available_from",
        "available_until",
    )
    list_filter = ("material", "session")
    ordering = ("-created_at",)
