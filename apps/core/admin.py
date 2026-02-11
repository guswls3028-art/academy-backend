# PATH: apps/core/admin.py
from django.contrib import admin

from apps.core.models import Tenant, TenantDomain, TenantMembership, Program


@admin.register(Tenant)
class TenantAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "code", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name", "code")


@admin.register(TenantDomain)
class TenantDomainAdmin(admin.ModelAdmin):
    list_display = ("id", "tenant", "host", "is_primary", "is_active")
    list_filter = ("is_primary", "is_active")


@admin.register(TenantMembership)
class TenantMembershipAdmin(admin.ModelAdmin):
    list_display = ("id", "tenant", "user", "role", "is_active", "joined_at")
    list_filter = ("role", "is_active")


@admin.register(Program)
class ProgramAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "tenant",
        "display_name",
        "brand_key",
        "login_variant",
        "plan",
        "is_active",
    )
    list_filter = ("login_variant", "plan", "is_active")
