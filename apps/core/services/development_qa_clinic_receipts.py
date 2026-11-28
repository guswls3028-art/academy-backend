"""Bind a disposable clinic receipt to its original route before the outbox drops the phone."""
import json

from django.conf import settings
from django.core.signing import BadSignature, Signer

from apps.core.models import OpsAuditLog
from apps.domains.messaging.security import build_recipient_fingerprint_candidates
from apps.domains.messaging.services.queue_service import build_enqueued_business_key
from apps.shared.utils.student_contact import contact_phone, student_contact_phone


FIELD = "qa_clinic_route_proof"
TRIGGERS = ("clinic_reservation_created", "clinic_cancelled")


def target_contact(tenant_id, role, target_id):
    """Return ownership (True, False, or absent) and only the source tenant's contact."""
    if role == "clinic_teacher":
        from django.contrib.auth import get_user_model

        targets = get_user_model()._base_manager.filter(pk=target_id)
        user = targets.filter(tenant_id=tenant_id).only("phone").first()
        if user:
            return True, contact_phone(user.phone)
    elif role in {"student", "parent"}:
        from apps.domains.students.models import Student

        targets = Student._base_manager.filter(pk=target_id)
        student = targets.filter(tenant_id=tenant_id).only("phone", "parent_phone", "uses_identifier").first()
        if student:
            return True, student_contact_phone(student, role)
    else:
        return False, ""
    return (False if targets.exists() else None), ""


def _route(payload, business_key, fingerprint):
    return json.dumps([
        payload.get("source_tenant_id"), payload.get("tenant_id"), payload.get("event_type"),
        payload.get("origin_type"), payload.get("origin_id"), payload.get("target_type"),
        str(payload.get("target_id")), payload.get("occurrence_key"), payload.get("template_id"), business_key, fingerprint,
    ], separators=(",", ":"))


def _signer():
    return Signer(
        key=settings.MESSAGING_TENANT_BINDING_KEY,
        fallback_keys=getattr(settings, "MESSAGING_TENANT_BINDING_FALLBACK_KEYS", ()),
        salt="academy.development.clinic-mock-route.v1",
    )


def terminal_proof(payload, trigger):
    source_id = payload.get("source_tenant_id")
    if (type(source_id) is not int or source_id <= 0 or not str(payload.get("target_id", "")).isdecimal()
            or not isinstance(payload.get("to"), str)
            or trigger not in TRIGGERS or payload.get("event_type") != trigger
            or payload.get("origin_type") != f"clinic.{trigger}"
            or payload.get("tenant_id") != getattr(settings, "OWNER_TENANT_ID", 1)
            or getattr(settings, "SOLAPI_KAKAO_PF_ID", "") != "development-mock-pfid"):
        return None
    if not OpsAuditLog.objects.filter(
        action="development.qa.scenario", result="success", target_tenant_id=source_id,
        target_tenant__code__startswith="qa-ymath-realuse-", payload__tenant_id=source_id,
    ).exists():
        return None
    owned, phone = target_contact(source_id, payload.get("target_type"), payload.get("target_id"))
    fingerprint = payload.get("recipient_fingerprint")
    if not (owned and phone and phone == contact_phone(payload.get("to"))
            and fingerprint in build_recipient_fingerprint_candidates(phone)):
        return None
    key = build_enqueued_business_key(tenant_id=source_id, payload=payload)
    return _signer().signature(_route(payload, key, fingerprint))


def verified_route(box, row):
    payload = box.payload
    owned, phone = target_contact(box.tenant_id, row.target_type, row.target_id)
    if owned is False:
        return False
    if FIELD in payload:
        route = _route(payload, box.business_idempotency_key, box.recipient_fingerprint)
        try:
            return _signer().unsign(f"{route}:{payload[FIELD]}") == route
        except BadSignature:
            return False
    # Existing outboxes can still prove their original key from the surviving scoped contact.
    return (bool(owned and phone) and row.recipient_fingerprint in build_recipient_fingerprint_candidates(phone)
            and build_enqueued_business_key(tenant_id=box.tenant_id, payload={**payload, "to": phone})
            == box.business_idempotency_key)
