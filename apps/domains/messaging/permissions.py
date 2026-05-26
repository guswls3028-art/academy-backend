from apps.core.models import TenantMembership


MESSAGE_SEND_ROLES = ("owner", "admin", "teacher")


def can_send_messages(request, tenant) -> bool:
    user = request.user
    if not user or not user.is_authenticated or not tenant:
        return False
    if TenantMembership.objects.filter(
        tenant=tenant,
        user=user,
        is_active=True,
        role__in=MESSAGE_SEND_ROLES,
    ).exists():
        return True
    return bool(user.is_superuser and getattr(user, "tenant_id", None) == tenant.id)
