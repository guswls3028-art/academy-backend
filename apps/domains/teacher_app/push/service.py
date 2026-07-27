"""Web Push delivery with endpoint validation and stale-subscription cleanup."""

import json
import logging

from django.conf import settings
from django.db.models import Q
from pywebpush import WebPushException, webpush

from apps.core.models import TenantMembership

from .models import PushSubscription
from .security import is_allowed_web_push_endpoint

logger = logging.getLogger(__name__)


class PlatformPushDeliveryError(RuntimeError):
    pass


def _deliver(sub: PushSubscription, payload: dict) -> bool:
    if not is_allowed_web_push_endpoint(sub.endpoint):
        sub.is_active = False
        sub.save(update_fields=["is_active", "updated_at"])
        logger.warning("Rejected stored push endpoint: sub=%s", sub.id)
        return False
    try:
        webpush(
            subscription_info={
                "endpoint": sub.endpoint,
                "keys": {
                    "p256dh": sub.p256dh_key,
                    "auth": sub.auth_key,
                },
            },
            data=json.dumps(payload, ensure_ascii=False),
            vapid_private_key=settings.VAPID_PRIVATE_KEY,
            vapid_claims={"sub": f"mailto:{settings.VAPID_CONTACT_EMAIL}"},
            timeout=10,
        )
        return True
    except WebPushException as exc:
        response = getattr(exc, "response", None)
        status_code = getattr(response, "status_code", None) if response else None
        if status_code in (404, 410):
            sub.is_active = False
            sub.save(update_fields=["is_active", "updated_at"])
            logger.info(
                "Push subscription deactivated: sub=%s code=%s",
                sub.id,
                status_code,
            )
            return False
        raise


def send_push_to_user(user_id: int, tenant_id: int, payload: dict) -> int:
    subscriptions = PushSubscription.objects.filter(
        user_id=user_id,
        tenant_id=tenant_id,
        app_scope=PushSubscription.AppScope.TEACHER,
        is_active=True,
    )
    sent = 0
    for sub in subscriptions:
        try:
            sent += int(_deliver(sub, payload))
        except Exception:
            logger.exception("Unexpected push error: sub=%s", sub.id)
    return sent


def send_push_to_staff(
    tenant_id: int,
    payload: dict,
    exclude_user_id: int | None = None,
) -> int:
    subs = PushSubscription.objects.filter(
        tenant_id=tenant_id,
        app_scope=PushSubscription.AppScope.TEACHER,
        is_active=True,
    )
    if exclude_user_id:
        subs = subs.exclude(user_id=exclude_user_id)

    sent = 0
    user_ids_seen = set()
    for sub in subs:
        if sub.user_id in user_ids_seen:
            continue
        user_ids_seen.add(sub.user_id)
        sent += send_push_to_user(sub.user_id, tenant_id, payload)
    return sent


def send_push_to_platform_admins(payload: dict) -> int:
    owner_tenant_id = getattr(settings, "OWNER_TENANT_ID", None)
    if not owner_tenant_id:
        raise PlatformPushDeliveryError("OWNER_TENANT_ID is not configured.")

    eligible_user_ids = TenantMembership.objects.filter(
        tenant_id=owner_tenant_id,
        is_active=True,
    ).filter(
        Q(user__is_superuser=True) | Q(role="owner"),
    ).values("user_id")
    subscriptions = list(
        PushSubscription.objects.filter(
            tenant_id=owner_tenant_id,
            user_id__in=eligible_user_ids,
            user__is_active=True,
            app_scope=PushSubscription.AppScope.PLATFORM,
            is_active=True,
        ).distinct()
    )
    if not subscriptions:
        raise PlatformPushDeliveryError("No active platform push subscriptions.")

    sent = 0
    transient_failures = 0
    for sub in subscriptions:
        try:
            sent += int(_deliver(sub, payload))
        except Exception as exc:
            transient_failures += 1
            logger.warning(
                "Platform push send failed: sub=%s error=%s",
                sub.id,
                exc.__class__.__name__,
            )
    if transient_failures:
        raise PlatformPushDeliveryError(
            f"{transient_failures} platform push deliveries failed transiently."
        )
    if not sent:
        raise PlatformPushDeliveryError("No active platform push endpoint accepted delivery.")
    return sent
