from __future__ import annotations

from dataclasses import dataclass


ALLOWED_OBSERVER_ROLES = frozenset({"owner", "admin", "staff"})


def _normalize_phone(value: str | None) -> str:
    return "".join(character for character in str(value or "") if character.isdigit())


@dataclass(frozen=True)
class MessagingObserverRecipient:
    user_id: int
    name: str
    phone: str


def get_messaging_observer_recipients(tenant_id: int) -> list[MessagingObserverRecipient]:
    """Resolve configured observers through their current active tenant membership."""
    from apps.domains.messaging.models import MessagingObserver

    assignments = (
        MessagingObserver.objects.select_related("user")
        .filter(
            tenant_id=tenant_id,
            user__is_active=True,
            user__tenant_memberships__tenant_id=tenant_id,
            user__tenant_memberships__is_active=True,
            user__tenant_memberships__role__in=ALLOWED_OBSERVER_ROLES,
        )
        .order_by("user_id")
        .distinct()
    )
    recipients = []
    seen_phones: set[str] = set()
    for assignment in assignments:
        phone = _normalize_phone(assignment.user.phone)
        if len(phone) != 11 or not phone.startswith("010") or phone in seen_phones:
            continue
        seen_phones.add(phone)
        recipients.append(
            MessagingObserverRecipient(
                user_id=assignment.user_id,
                name=(assignment.user.name or "").strip(),
                phone=phone,
            )
        )
    return recipients


def build_messaging_observer_payloads(
    *,
    original_outbox,
    recipients: list[MessagingObserverRecipient],
) -> list[dict]:
    """Clone one durable payload for observers without changing the original target."""
    original_payload = dict(original_outbox.payload)
    if (
        original_payload.get("target_type") == "messaging_observer"
        or original_payload.get("origin_type") == "messaging_observer"
    ):
        return []

    original_phone = _normalize_phone(original_payload.get("to"))
    payloads = []
    for recipient in recipients:
        if recipient.phone == original_phone:
            continue
        observer_payload = dict(original_payload)
        observer_payload.update(
            {
                "to": recipient.phone,
                "target_type": "messaging_observer",
                "target_id": f"user:{recipient.user_id}",
                "target_name": recipient.name,
                "origin_type": "messaging_observer",
                "origin_id": f"outbox:{original_outbox.id}",
            }
        )
        payloads.append(observer_payload)
    return payloads
