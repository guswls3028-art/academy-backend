"""
Web Push 발송 서비스.
pywebpush로 VAPID 서명 후 구독 엔드포인트에 전달.
404/410 응답 시 구독 비활성화.
"""
import json
import logging

from django.conf import settings
from pywebpush import webpush, WebPushException

from .models import PushSubscription

logger = logging.getLogger(__name__)


def send_push_to_user(user_id: int, tenant_id: int, payload: dict) -> int:
    """
    특정 사용자의 활성 구독 전체에 푸시 전송.
    Returns: 성공 발송 건수.
    """
    subscriptions = PushSubscription.objects.filter(
        user_id=user_id,
        tenant_id=tenant_id,
        is_active=True,
    )
    sent = 0
    for sub in subscriptions:
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
            sent += 1
        except WebPushException as e:
            status = getattr(e, "response", None)
            status_code = getattr(status, "status_code", None) if status else None
            if status_code in (404, 410):
                sub.is_active = False
                sub.save(update_fields=["is_active", "updated_at"])
                logger.info("Push subscription deactivated: sub=%s code=%s", sub.id, status_code)
            else:
                logger.warning("Push send failed: sub=%s err=%s", sub.id, e)
        except Exception:
            logger.exception("Unexpected push error: sub=%s", sub.id)
    return sent


def send_push_to_staff(tenant_id: int, payload: dict, exclude_user_id: int | None = None) -> int:
    """
    테넌트 내 모든 활성 스태프에게 푸시 전송.
    exclude_user_id: 본인 제외 (자기 행동에 대한 알림 방지).
    """
    subs = PushSubscription.objects.filter(
        tenant_id=tenant_id,
        is_active=True,
    )
    if exclude_user_id:
        subs = subs.exclude(user_id=exclude_user_id)

    sent = 0
    user_ids_seen = set()
    for sub in subs.select_related():
        if sub.user_id in user_ids_seen:
            continue
        user_ids_seen.add(sub.user_id)
        sent += send_push_to_user(sub.user_id, tenant_id, payload)
    return sent
