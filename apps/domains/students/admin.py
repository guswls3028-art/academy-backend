from django.contrib import admin
from .models import Student, Tag, StudentTag


@admin.register(Student)
class StudentAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "name",
        "gender",
        "grade",
        "phone",
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
    search_fields = ("name", "phone")


@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "color")
    search_fields = ("name",)


@admin.register(StudentTag)
class StudentTagAdmin(admin.ModelAdmin):
    list_display = ("student", "tag")
    list_filter = ("tag",)
