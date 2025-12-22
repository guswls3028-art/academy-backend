from django.contrib import admin
from .models import (
    Staff,
    WorkType,
    StaffWorkType,
    WorkRecord,
    ExpenseRecord,
)


@admin.register(Staff)
class StaffAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "phone", "is_active", "is_manager")
    search_fields = ("name", "phone")
    list_filter = ("is_active", "is_manager", "pay_type")


@admin.register(WorkType)
class WorkTypeAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "base_hourly_wage", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name",)


@admin.register(StaffWorkType)
class StaffWorkTypeAdmin(admin.ModelAdmin):
    list_display = ("staff", "work_type", "hourly_wage")


@admin.register(WorkRecord)
class WorkRecordAdmin(admin.ModelAdmin):
    list_display = ("date", "staff", "work_type", "work_hours", "amount")
    list_filter = ("date", "work_type")


@admin.register(ExpenseRecord)
class ExpenseRecordAdmin(admin.ModelAdmin):
    list_display = ("date", "staff", "title", "amount", "status")
    list_filter = ("status",)
