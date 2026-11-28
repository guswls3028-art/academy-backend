"""Worker-side scope for one disposable late booking in isolated development."""

import re
from uuid import UUID

from .qa_ack import _Scope, _development_release, sealed_delivery


class _ClinicScope(_Scope):
    prefix = "development.qa.clinic-notice."

    def _log_target(self):
        p = self.payload
        target_id = p["teacher_id"] if p["role"] == "clinic_teacher" else p["student_id"]
        return {"target_type": p["role"], "target_id": str(target_id), "origin_id": p["origin_id"]}, "clinic_reservation_created"


def clinic_ack_scope(cfg, raw, data, owner_id, source_id):
    origin = str(data.get("domain_object_id") or "")
    match = re.fullmatch(r"clinic_participant:([1-9][0-9]{0,17}):(reservation_pending|approval)", origin)
    role = data.get("target_type")
    if (not match or role not in {"student", "parent", "clinic_teacher"}
            or not source_id or source_id == owner_id or owner_id != cfg.OWNER_TENANT_ID):
        return None

    from apps.core.models import OpsAuditLog

    try:
        plan = OpsAuditLog.objects.get(
            action=_ClinicScope.prefix + "plan", actor_username="clinic-notice-development-runner",
            target_tenant_id=source_id, target_tenant__code__startswith="qa-ymath-realuse-wn-", result="success",
        )
    except OpsAuditLog.DoesNotExist:
        return None

    from apps.domains.clinic.models import SessionParticipant
    from apps.domains.messaging.alimtalk_content_builders import SOLAPI_CLINIC_INFO
    from apps.domains.messaging.models import ScheduledNotification

    p = plan.payload
    release = _development_release(cfg.MESSAGING_SQS_QUEUE_NAME)
    if (not release or p.get("release") != release or p.get("schema") != "clinic-notice-worker/v1"
            or p.get("tenant_id") != source_id or p.get("redelivery_role") != "clinic_teacher"
            or str(UUID(plan.summary)) != plan.summary or p.get("request_id") != plan.summary
            or any(type(p.get(k)) is not int or p[k] < 1 for k in ("actor_id", "teacher_id", "student_id", "session_id"))
            or p["actor_id"] != p["teacher_id"]):
        raise ValueError("Clinic notice QA plan does not match the isolated release")
    participant_id, phase = int(match[1]), match[2]
    pending = phase == "reservation_pending"
    target_id = p["teacher_id"] if role == "clinic_teacher" else p["student_id"]
    if ((role == "clinic_teacher" and not pending) or data.get("origin_id") != origin
            or data.get("target_id") != str(target_id) or data.get("template_id") != SOLAPI_CLINIC_INFO
            or data.get("message_mode") != "alimtalk"):
        raise ValueError("Clinic notice QA recipient or template differs from the sealed plan")
    participant = SessionParticipant.objects.filter(
        pk=participant_id, tenant_id=source_id, session_id=p["session_id"], session__tenant_id=source_id,
        session__notification_teacher_id=p["teacher_id"], session__created_by_id=p["actor_id"],
        student_id=p["student_id"], student__tenant_id=source_id,
        requested_after_cutoff=True, source="student_request", status="pending" if pending else "booked",
        created_at__gte=plan.created_at,
    )
    if not pending:
        participant = participant.filter(status_changed_by_id=p["actor_id"])
    if not participant.exists():
        raise ValueError("Clinic notice QA participant differs from the sealed plan")
    key = str(data.get("business_idempotency_key") or "")
    if not re.fullmatch(r"[0-9a-f]{64}", key):
        raise ValueError("Clinic notice QA has no valid business key")
    outbox_id = ScheduledNotification.objects.values_list("pk", flat=True).get(
        tenant_id=source_id, origin_id=origin, business_idempotency_key=key, trigger="clinic_reservation_created",
        created_at__gte=plan.created_at, payload__tenant_id=owner_id, payload__source_tenant_id=source_id,
        payload__target_type=role,
    )
    return _ClinicScope({
        **sealed_delivery(plan, source_id, raw), "schema": p["schema"], "release": release, "plan_id": plan.pk,
        "request_id": plan.summary, "actor_id": p["actor_id"], "source_tenant_id": source_id,
        "owner_tenant_id": owner_id, "participant_id": participant_id, "session_id": p["session_id"],
        "student_id": p["student_id"], "teacher_id": p["teacher_id"], "origin_id": origin, "role": role,
        "outbox_id": outbox_id, "business_key": key,
    }, pending and role == "clinic_teacher")
