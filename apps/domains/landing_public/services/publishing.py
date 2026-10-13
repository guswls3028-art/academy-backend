"""The same two active tenant publishers manage public resources and videos."""
from django.contrib.auth import get_user_model
from rest_framework.exceptions import PermissionDenied

from apps.core.models import TenantMembership
from apps.core.services.tenant_access import get_authorized_tenant_role
from apps.domains.landing_public.models.resource import PublicResourceBoardAccess

PUBLISHER_ROLES = ("owner", "admin", "teacher")


def can_publish(request):
    user, tenant = request.user, request.tenant
    if not user or not user.is_authenticated or not user.is_active:
        return False
    access = PublicResourceBoardAccess.objects.filter(tenant=tenant).first()
    if not access or not access.publisher_one_id or not access.publisher_two_id:
        return False
    return (
        user.id in (access.publisher_one_id, access.publisher_two_id)
        and get_authorized_tenant_role(user, tenant) in PUBLISHER_ROLES
    )


def require_publisher(request, *, lock=False):
    if lock:
        # Shared with membership revocation: User -> membership -> publisher access.
        user = get_user_model().objects.select_for_update().filter(pk=request.user.pk).first()
        if not user or not user.is_active:
            raise PermissionDenied("자료 게시 권한이 없습니다.")
        request.user.is_active = user.is_active
        TenantMembership.objects.select_for_update().filter(tenant=request.tenant, user=user).first()
        PublicResourceBoardAccess.objects.select_for_update().filter(tenant=request.tenant).first()
    if not can_publish(request):
        raise PermissionDenied("자료 게시 권한이 없습니다.")
