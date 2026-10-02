"""Manual-send admission and trace projections; never infer recipient delivery."""

import hashlib
import json
from dataclasses import dataclass, field
from datetime import timezone as datetime_timezone
from uuid import UUID, uuid4

from django.db import transaction
from rest_framework.response import Response

from apps.core.models import Tenant
from apps.domains.messaging.models import ManualSendRequest, ScheduledNotification


@dataclass
class ManualSendAdmission:
    request_id: UUID
    recipient_scope: str
    outbox_ids: list[int] = field(default_factory=list)

    @property
    def delivery_metadata(self) -> dict:
        return {
            "origin_type": "manual_send",
            "origin_id": str(self.request_id),
            "occurrence_key": f"dispatch:manual:{self.request_id}:{self.recipient_scope}",
        }


def _payload_fingerprint(data: dict) -> str:
    # Both target scopes are parts of the same draft. Resolved template/phone
    # snapshots are deliberately absent: a replay uses the first outbox.
    schedule = data.get("scheduled_send_at")
    canonical = {
        "student_ids": sorted(set(data.get("student_ids") or [])),
        "staff_ids": sorted(set(data.get("staff_ids") or [])),
        "message_mode": data.get("message_mode") or "alimtalk",
        "template_id": data.get("template_id"),
        "raw_body": (data.get("raw_body") or "").strip(),
        "raw_subject": (data.get("raw_subject") or "").strip(),
        "scheduled_send_at": schedule.astimezone(datetime_timezone.utc).isoformat() if schedule else None,
        "block_category": (data.get("block_category") or "").strip(),
        "alimtalk_extra_vars": data.get("alimtalk_extra_vars") or {},
        "alimtalk_extra_vars_per_student": {
            str(key): {str(name): value for name, value in values.items()}
            for key, values in (data.get("alimtalk_extra_vars_per_student") or {}).items()
        },
    }
    encoded = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def project_request_outboxes(receipts) -> dict:
    receipts = list(receipts)
    outbox_ids = {int(pk) for receipt in receipts for pk in receipt.outbox_ids}
    rows = list(ScheduledNotification.objects.filter(
        tenant_id=receipts[0].tenant_id if receipts else None,
        pk__in=outbox_ids,
    ).values("status", "sent_at"))
    return {
        "accepted_count": len(outbox_ids),
        "enqueued": sum(row["sent_at"] is not None for row in rows),
        "scheduled": sum(row["status"] in {
            ScheduledNotification.Status.PENDING, ScheduledNotification.Status.DISPATCHING,
        } for row in rows),
        "enqueue_failed": sum(row["status"] == ScheduledNotification.Status.FAILED for row in rows),
        "cancelled_count": sum(row["status"] == ScheduledNotification.Status.CANCELLED for row in rows),
        "skipped_no_phone": sum(receipt.skipped_no_phone for receipt in receipts),
    }


def _receipt_response(receipt, *, replayed: bool) -> Response:
    counts = project_request_outboxes([receipt])
    failed = counts["enqueue_failed"]
    response_status = 503 if failed and not counts["enqueued"] and not counts["scheduled"] else 200
    return Response({
        "detail": (
            f"발송 요청 접수 {counts['accepted_count']}건 "
            f"(큐 등록 {counts['enqueued']}건, 대기 {counts['scheduled']}건, 큐 등록 실패 {failed}건)."
        ),
        "request_id": str(receipt.request_id),
        "replayed": replayed,
        **counts,
    }, status=response_status)


def execute_manual_send_request(*, tenant, actor_user_id: int, data: dict, send) -> Response:
    request_id = data.get("client_request_id") or uuid4()
    fingerprint = _payload_fingerprint(data)
    recipient_scope = data["send_to"]
    replayed = False
    with transaction.atomic():
        # Lock the business tenant before inspecting or inserting a receipt.
        # This also serializes the initially missing UUID across target scopes.
        Tenant.objects.select_for_update().get(pk=tenant.pk)
        existing = list(ManualSendRequest.objects.filter(tenant=tenant, request_id=request_id))
        if any(row.actor_user_id != actor_user_id or row.payload_fingerprint != fingerprint for row in existing):
            return Response({
                "detail": "같은 요청 식별자는 최초 요청자와 동일한 발송 내용에만 사용할 수 있습니다.",
                "code": "manual_request_conflict",
            }, status=409)
        receipt = next((row for row in existing if row.recipient_scope == recipient_scope), None)
        if receipt is not None:
            replayed = True
        else:
            admission = ManualSendAdmission(request_id=request_id, recipient_scope=recipient_scope)
            result = send(admission)
            if result.status_code >= 400:
                transaction.set_rollback(True)
                return result
            if not admission.outbox_ids:
                transaction.set_rollback(True)
                return Response({
                    "detail": "발송 가능한 전화번호가 없습니다." if result.data.get("skipped_no_phone") else "발송 요청을 저장하지 못했습니다.",
                    "code": "no_accepted_recipients",
                    "accepted_count": 0,
                    "request_id": str(request_id),
                }, status=422 if result.data.get("skipped_no_phone") else 503)
            receipt = ManualSendRequest.objects.create(
                tenant=tenant,
                request_id=request_id,
                recipient_scope=recipient_scope,
                actor_user_id=actor_user_id,
                payload_fingerprint=fingerprint,
                outbox_ids=admission.outbox_ids,
                skipped_no_phone=result.data.get("skipped_no_phone", 0),
            )
    # Dispatch callbacks run after commit. Within an outer business transaction,
    # pending stays pending; we never label the callback's future work enqueued.
    return _receipt_response(receipt, replayed=replayed)


def request_trace(receipts, logs) -> dict:
    receipts = list(receipts)
    return {
        "request_id": str(receipts[0].request_id),
        **project_request_outboxes(receipts),
        "provider_accepted_count": logs.filter(status="sent", success=True).count(),
        "provider_pending_count": logs.filter(status__in=["processing", "sending", "retryable_failed"]).count(),
        "provider_failed_count": logs.filter(status="failed").count(),
        "provider_ambiguous_count": logs.filter(status="ambiguous").count(),
        "delivered_count": None,
    }
