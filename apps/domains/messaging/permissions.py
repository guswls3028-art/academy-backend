from apps.core.services.tenant_access import get_authorized_tenant_role


MESSAGE_SEND_ROLES = ("owner", "admin", "teacher")


def can_send_messages(request, tenant) -> bool:
    user = request.user
    if not user or not user.is_authenticated or not tenant:
        return False
    return get_authorized_tenant_role(user, tenant) in MESSAGE_SEND_ROLES


def can_manage_messaging_settings(request, tenant) -> bool:
    """Only tenant owner/admin may inspect or mutate shared messaging operations."""
    user = request.user
    if not user or not user.is_authenticated or not tenant:
        return False
    return get_authorized_tenant_role(user, tenant) in ("owner", "admin")
