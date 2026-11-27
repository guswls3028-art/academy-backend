"""SQS evidence for the fixed, two-student development registration scenario."""

import logging
import os
import re
import sys
import time
from dataclasses import dataclass
from types import SimpleNamespace
from uuid import UUID


QUEUE = "academy-v1-development-messaging-queue"
PREFIX = "development.qa.enrollment-notice."
PLAN = PREFIX + "plan"
MOCK_SEND = PREFIX + "mock-send"
REDELIVERY = PREFIX + "redelivery"
ACK = PREFIX + "ack"
SCHEMA = "enrollment-notice-worker/v1"
WORKER = "MESSAGING"
logger = logging.getLogger(__name__)


def _development_release(queue_name):
    if (os.environ.get("ACADEMY_RUNTIME_ENV") != "development"
            or queue_name != QUEUE or os.environ.get("MESSAGING_SQS_QUEUE_NAME") != QUEUE
            or os.environ.get("DJANGO_SETTINGS_MODULE") != "apps.api.config.settings.worker"
            or os.environ.get("DB_NAME") != "academy_api_development"
            or os.environ.get("DB_USER") != "academy_api_development_app"
            or os.environ.get("SOLAPI_MOCK", "").lower() != "true"
            or os.environ.get("SOLAPI_KAKAO_PF_ID") != "development-mock-pfid"
            or os.environ.get("MESSAGING_DRY_RUN_TRIGGERS", "").strip()
            or getattr(getattr(sys.modules["__main__"], "__spec__", None), "name", None)
            != "apps.worker.messaging_worker.sqs_main"):
        return None
    release = os.environ.get("ACADEMY_DEVELOPMENT_RELEASE_ID", "")
    if re.fullmatch(r"sha-[0-9a-f]{40}-run-[0-9]+-[0-9]+|manual-sha256-[0-9a-f]{64}", release):
        return release
    return None


def enrollment_ack_scope(cfg, raw, data, owner_id, source_id):
    """Return None for ordinary jobs without importing ORM or looking up production data."""
    if os.environ.get("ACADEMY_RUNTIME_ENV") != "development":
        return None
    marker = re.fullmatch(r"enrollment-notice:([1-9][0-9]{0,17})", str(data.get("domain_object_id", "")))
    if (not marker or owner_id != cfg.OWNER_TENANT_ID or not source_id
            or source_id == owner_id or data.get("target_type") != "account"
            or data.get("event_type") != "registration_approved_student"):
        return None

    from apps.core.models import OpsAuditLog
    from apps.core.services.development_qa_ownership import WRONG_NOTE_SETUP_SCHEMA
    from apps.domains.enrollment.models import EnrollmentNoticeRecipient

    recipient = EnrollmentNoticeRecipient.objects.select_related("registration").filter(
        pk=int(marker[1]), role="student", registration__tenant_id=source_id,
        registration__lecture__tenant_id=source_id, status="admitted", outbox_id__isnull=False,
        business_key=data.get("business_idempotency_key") or "",
        registration__student_notice="send", registration__parent_notice="skip",
    ).first()
    if not recipient or data.get("target_id") != f"student:{recipient.student_id}" or not recipient.business_key:
        return None
    batch = recipient.registration
    request_id = str(batch.request_id)
    plan = OpsAuditLog.objects.filter(action=PLAN, summary=request_id, target_tenant_id=source_id,
                                      result="success", actor_username="enrollment-notice-development-runner")
    plans = list(plan[:2])
    if not plans:
        return None
    if len(plans) != 1:
        raise ValueError("Ambiguous enrollment notice QA plan")
    plan = plans[0]
    fields = plan.payload
    actor_id = fields.get("actor_id")
    if type(actor_id) is not int or actor_id < 1:
        raise ValueError("Enrollment notice QA plan has no registration actor")
    if actor_id != batch.actor_id:
        return None
    release = _development_release(cfg.MESSAGING_SQS_QUEUE_NAME)
    if not release:
        raise ValueError("Enrollment notice QA requires the dedicated development mock worker")
    students = fields.get("student_ids")
    if (fields.get("schema") != SCHEMA or fields.get("release") != release
            or fields.get("tenant_id") != source_id or fields.get("request_id") != request_id
            or fields.get("lecture_id") != batch.lecture_id or not isinstance(students, list)
            or len(students) != 2 or any(type(s) is not int or s < 1 for s in students)
            or students[0] == students[1] or sorted(students) != sorted(batch.student_ids)
            or recipient.student_id not in students or fields.get("redelivery_student_id") not in students
            or plan.created_at > batch.created_at):
        raise ValueError("Enrollment notice QA plan does not match registration")
    setup = OpsAuditLog.objects.filter(
        pk=fields.get("setup_id"), action="development.wrong-note.setup", actor_username="wrong-note-development-runner",
        result="success", target_tenant_id=source_id, target_tenant__is_active=True, payload__tenant_id=source_id,
        created_at__lte=plan.created_at,
    ).values("pk", "payload", "target_tenant__code").first()
    if (not setup or setup["payload"].get("schema") != WRONG_NOTE_SETUP_SCHEMA
            or setup["payload"].get("tenant_code") != setup["target_tenant__code"]
            or not re.fullmatch(r"qa-ymath-realuse-wn-[0-9]+-[0-9]+-[0-9a-f]{12}", setup["target_tenant__code"])
            or not re.fullmatch(r"[0-9a-f]{64}", str(setup["payload"].get("owner_sha256")))):
        raise ValueError("Enrollment notice QA ownership mismatch")
    # Only the authentic SQS envelope supplies these values. Never use ReceiptHandle as an ID.
    message_id = str(UUID(raw["MessageId"]))
    receive_count = int(raw["Attributes"]["ApproximateReceiveCount"])
    if receive_count < 1:
        raise ValueError("Invalid SQS receive count")
    return _Scope({
        "schema": SCHEMA, "setup_id": setup["pk"], "plan_id": plan.pk, "release": release,
        "request_id": request_id, "actor_id": actor_id, "source_tenant_id": source_id, "owner_tenant_id": owner_id,
        "recipient_id": recipient.pk, "student_id": recipient.student_id, "role": recipient.role,
        "business_key": recipient.business_key, "message_id": message_id, "queue": QUEUE,
        "receive_count": receive_count,
    }, recipient.student_id == fields["redelivery_student_id"])


@dataclass
class _Scope:
    payload: dict
    redeliver: bool

    def _log(self, *, log_id, committed):
        from apps.domains.messaging.models import NotificationLog

        p = self.payload
        rows = NotificationLog.objects.filter(
            tenant_id=p["owner_tenant_id"], tenant__code="academy-development-owner", tenant__is_active=True,
            source_tenant_id=p["source_tenant_id"], target_type="account",
            target_id=f"student:{p['student_id']}", message_mode="alimtalk",
            business_idempotency_key=p["business_key"], sqs_message_id=p["message_id"],
        )
        if log_id is not None:
            rows = rows.filter(pk=log_id)
        if committed:
            rows = rows.filter(status="sent", success=True, notification_type="registration_approved_student",
                               provider_reference_kind="group")
        else:
            rows = rows.filter(pk=log_id, status="sending")
        return rows.values("pk", "provider_message_id").get()

    def _events(self):
        from apps.core.models import OpsAuditLog

        return OpsAuditLog.objects.filter(summary=self.payload["request_id"], actor_username=WORKER,
                                          target_tenant_id=self.payload["source_tenant_id"], result="success")

    def _record(self, action, log_id, **extra):
        from apps.core.models import OpsAuditLog

        event = dict(
            action=action, summary=self.payload["request_id"], actor_username=WORKER, result="success",
            target_tenant_id=self.payload["source_tenant_id"], payload={**self.payload, "log_id": log_id, **extra},
        )
        if action != ACK:
            OpsAuditLog.objects.create(**event)
            return
        # SQS has already deleted the message. Retry this exact receipt if the DB
        # connection drops, including a lost response to an already committed insert.
        from django.db import OperationalError, close_old_connections

        for attempt in range(3):
            try:
                OpsAuditLog.objects.get_or_create(**event)
                return
            except OperationalError:
                if attempt == 2:
                    raise
                close_old_connections()
                time.sleep(0.25 * (attempt + 1))

    def observe_mock(self, client, log_id):
        """Capture every completed call to the exact mock at its send boundary."""
        from apps.domains.messaging.solapi_mock import MockSolapiMessageService

        if type(client) is not MockSolapiMessageService:
            raise ValueError("Sealed development job requires the mock provider")
        self._log(log_id=log_id, committed=False)

        def send(message):
            response = client.send(message)
            self._record(MOCK_SEND, log_id, provider_message_id=response.group_info.group_id)
            return response

        return SimpleNamespace(send=send)

    def committed_log(self, log_id):
        row = self._log(log_id=log_id, committed=True)
        if not re.fullmatch(r"mock-[0-9a-f]{12}", row["provider_message_id"]):
            raise ValueError("Sealed development log has no mock receipt")
        return row["pk"]

    def observed_first_delivery(self, log_id):
        return self._events().filter(action=REDELIVERY, payload={**self.payload, "log_id": log_id,
                                                                  "receive_count": 1}).exists()


def acknowledge_sent(queue, queue_name, handle, scope, *, log_id, deduplicated):
    """Return (actual SQS ACK, deliberate lock release for a safe retry)."""
    if scope is None:
        return queue.delete_message(queue_name=queue_name, receipt_handle=handle), False
    log_id = scope.committed_log(log_id)
    first = scope.payload["receive_count"] == 1
    if scope.redeliver and first and not deduplicated:
        # The sent row is already durable. Shorten only this receipt's visibility.
        changed = queue.change_message_visibility(queue_name=queue_name, receipt_handle=handle, visibility_timeout=3)
        if changed:
            scope._record(REDELIVERY, log_id)
        return False, changed
    proven = not scope.redeliver or (not first and deduplicated and scope.observed_first_delivery(log_id))
    deleted = queue.delete_message(queue_name=queue_name, receipt_handle=handle)
    if deleted and proven:
        # SQS deletion is irreversible. A missing audit receipt fails the fixed probe.
        try:
            scope._record(ACK, log_id, deduplicated=deduplicated)
        except Exception:
            logger.exception("Could not persist sealed development enrollment notice ACK")
    return deleted, False
