from django.contrib import admin
from .models import Student, Tag, StudentTag


@admin.register(Student)
class StudentAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "ps_number",   # ✅ NEW
        "omr_code",    # ✅ NEW
        "name",
        "gender",
        "grade",
        "phone",
        "parent_phone",  # ✅ 운영 확인용
        "parent",
        "high_school",
        "is_managed",
        "created_at",
    )
    list_filter = (
        "gender",
        "grade",
        "high_school",
        "is_managed",
    )
    search_fields = ("ps_number", "omr_code", "name", "phone")


@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "color")
    search_fields = ("name",)


@admin.register(StudentTag)
class StudentTagAdmin(admin.ModelAdmin):
    list_display = ("student", "tag")
    list_filter = ("tag",)
