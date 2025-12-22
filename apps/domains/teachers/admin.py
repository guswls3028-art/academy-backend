from django.contrib import admin
from .models import Teacher


@admin.register(Teacher)
class TeacherAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "subject", "phone", "is_active", "created_at")
    search_fields = ("name", "phone", "subject")
    list_filter = ("subject", "is_active")
