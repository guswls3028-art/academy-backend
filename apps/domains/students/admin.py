from django.contrib import admin
from .models import Student, Tag, StudentTag


class TenantScopedAdmin(admin.ModelAdmin):
    """Admin mixin that filters queryset by tenant and shows tenant in list."""

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        # Staff users only see their own tenant's data
        memberships = request.user.tenant_memberships.filter(
            is_active=True, role__in=["owner", "admin", "teacher", "staff"]
        )
        tenant_ids = memberships.values_list("tenant_id", flat=True)
        return qs.filter(tenant_id__in=tenant_ids)

    def has_change_permission(self, request, obj=None):
        if obj and not request.user.is_superuser:
            memberships = request.user.tenant_memberships.filter(
                is_active=True, tenant=obj.tenant
            )
            if not memberships.exists():
                return False
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        if obj and not request.user.is_superuser:
            memberships = request.user.tenant_memberships.filter(
                is_active=True, tenant=obj.tenant
            )
            if not memberships.exists():
                return False
        return super().has_delete_permission(request, obj)


@admin.register(Student)
class StudentAdmin(TenantScopedAdmin):
    list_display = (
        "id",
        "tenant",
        "ps_number",
        "omr_code",
        "name",
        "gender",
        "grade",
        "phone",
        "parent_phone",
        "parent",
        "high_school",
        "is_managed",
        "created_at",
    )
    list_filter = (
        "tenant",
        "gender",
        "grade",
        "high_school",
        "is_managed",
    )
    search_fields = ("ps_number", "omr_code", "name", "phone")


@admin.register(Tag)
class TagAdmin(TenantScopedAdmin):
    list_display = ("id", "tenant", "name", "color")
    list_filter = ("tenant",)
    search_fields = ("name",)


@admin.register(StudentTag)
class StudentTagAdmin(admin.ModelAdmin):
    list_display = ("student", "tag", "get_tenant")
    list_filter = ("tag",)
    raw_id_fields = ("student", "tag")

    @admin.display(description="Tenant")
    def get_tenant(self, obj):
        return obj.student.tenant if obj.student else None

    def get_queryset(self, request):
        qs = super().get_queryset(request).select_related("student__tenant")
        if request.user.is_superuser:
            return qs
        memberships = request.user.tenant_memberships.filter(
            is_active=True, role__in=["owner", "admin", "teacher", "staff"]
        )
        tenant_ids = memberships.values_list("tenant_id", flat=True)
        return qs.filter(student__tenant_id__in=tenant_ids)
