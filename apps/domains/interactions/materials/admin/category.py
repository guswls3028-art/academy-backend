from django.contrib import admin
from ..models import MaterialCategory


@admin.register(MaterialCategory)
class MaterialCategoryAdmin(admin.ModelAdmin):
    list_display = ("id", "lecture", "name", "order")
    list_filter = ("lecture",)
    ordering = ("lecture", "order")
