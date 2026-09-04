"""Operational state receipts and delivery, isolated from business writers."""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import urllib.error
import urllib.request
from uuid import uuid4

from django.conf import settings
from django.db import connection
from django.utils import timezone

from apps.core.models import OpsAuditLog
from apps.support.progress.state_detector_dependencies import RULE, InspectionFailure, inspect_session_exam_state

TRANSITION_ACTION = "monitor.state_integrity.transition"
CHECK_ACTION = "monitor.state_integrity.check"


def _lock_key(tenant_id):
    return int.from_bytes(hashlib.sha256(f"{RULE}:{tenant_id}".encode()).digest()[:8], "big", signed=True)


@contextmanager
def _exclusive_monitor(tenant_id):
    # Session lock spans the committed pending receipt and the network request.
    # No product row is locked. PostgreSQL is required for cross-process dedup.
    if connection.vendor != "postgresql" or connection.in_atomic_block:
        raise InspectionFailure("postgresql_autocommit_required")
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock(%s)", [_lock_key(tenant_id)])
        if not cursor.fetchone()[0]:
            raise InspectionFailure("monitor_busy")
    try:
        yield
    finally:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_unlock(%s)", [_lock_key(tenant_id)])


def _deliver(receiver, payload):
    try:
        request = urllib.request.Request(
            receiver, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}, method="POST"
        )
        if request.type not in {"http", "https"}:
            return "failed"
    except ValueError:
        return "failed"
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            return "delivered" if 200 <= response.status < 300 else "failed"
    except urllib.error.HTTPError:
        return "failed"
    except Exception:
        # A timeout may follow acceptance. Preserve uncertainty instead of
        # automatically duplicating an unacknowledged external notification.
        return "unknown"


def _receipt(tenant_id, payload, *, action=TRANSITION_ACTION):
    return OpsAuditLog.objects.create(
        action=action,
        target_tenant_id=tenant_id,
        summary="State detector " + payload.get("event", "inspection"),
        result="failed"
        if payload.get("delivery_status") in {"failed", "unknown"} or payload.get("inspection_status") == "failed"
        else "success",
        payload=payload,
    )


def public_report(report):
    return {key: value for key, value in report.items() if not key.startswith("_")}


def _process_observation(report, *, tenant_id):
    if report["inspection_status"] != "complete":
        report["delivery_status"] = "not_attempted"
        _receipt(tenant_id, public_report(report), action=CHECK_ACTION)
        return report
    last = (
        OpsAuditLog.objects.filter(action=TRANSITION_ACTION, target_tenant_id=tenant_id, payload__rule=RULE)
        .order_by("-id")
        .first()
    )
    previous = last.payload if last else None
    if (
        previous
        and previous["state"] == "contradiction"
        and not set(previous.get("finding_subjects", [])).issubset(report["_covered_subjects"])
    ):
        report["inspection_status"] = "failed"
        report["state"] = "unknown"
        report["errors"].append("previous_subject_missing")
        report["delivery_status"] = "not_attempted"
        _receipt(tenant_id, public_report(report), action=CHECK_ACTION)
        return report
    receiver = str(getattr(settings, "DEV_ALERTS_WEBHOOK_URL", "") or "").strip()
    if not receiver:
        report["delivery_status"] = "failed"
        report["errors"].append("receiver_missing")
        _receipt(tenant_id, public_report(report), action=CHECK_ACTION)
        return report
    same = previous and previous["state"] == report["state"] and previous["fingerprint"] == report["fingerprint"]
    if same and previous["delivery_status"] == "delivered":
        report.update(delivery_status="suppressed", event=previous["event"], event_id=previous["event_id"])
        return report
    if previous and previous["delivery_status"] in {"pending", "unknown"}:
        report["delivery_status"] = "unknown"
        report["errors"].append("delivery_reconciliation_required")
        return report
    if not previous and report["state"] == "healthy":
        report["delivery_status"] = "not_required"
        return report
    event = (
        previous["event"]
        if same
        else "recovered"
        if report["state"] == "healthy"
        else "changed"
        if previous and previous["state"] == "contradiction"
        else "opened"
    )
    event_id = previous["event_id"] if same else uuid4().hex
    report.update(event=event, event_id=event_id, delivery_status="pending")
    payload = {**public_report(report), "finding_subjects": report["_finding_subjects"]}
    # Append-only records preserve both the attempted transition and every
    # delivery outcome. A crash leaves pending, requiring reconciliation.
    _receipt(tenant_id, payload)
    delivery = _deliver(
        receiver,
        {
            "text": f"Academy state detector | tenant={tenant_id} | rule={RULE} | event={event} | findings={report['finding_count']} | fingerprint={report['fingerprint']} | event_id={event_id}"
        },
    )
    if delivery not in {"delivered", "failed", "unknown"}:
        delivery = "unknown"
    report["delivery_status"] = delivery
    try:
        _receipt(tenant_id, {**payload, "delivery_status": delivery})
    except Exception:
        # The pending receipt is durable but the acknowledgment is not. Do not
        # report success or automatically resend after a possible acceptance.
        report["delivery_status"] = "unknown"
        raise
    return report


def run_state_detector(*, tenant_id, dry_run=False, limit=200):
    report = None
    try:
        if dry_run:
            report = inspect_session_exam_state(tenant_id=tenant_id, limit=limit, now=timezone.now())
            report["delivery_status"] = "not_attempted"
            return public_report(report)
        if isinstance(tenant_id, bool) or not isinstance(tenant_id, int) or tenant_id <= 0:
            raise InspectionFailure("tenant_required")
        with _exclusive_monitor(tenant_id):
            report = inspect_session_exam_state(tenant_id=tenant_id, limit=limit, now=timezone.now())
            # Unknown tenant must not become a ledger FK or default tenant.
            if "tenant_not_found" in report["errors"]:
                report["delivery_status"] = "not_attempted"
            else:
                report = _process_observation(report, tenant_id=tenant_id)
    except InspectionFailure as exc:
        report = report or {"rule": RULE, "state": "unknown", "finding_count": 0, "errors": []}
        report.update(inspection_status="failed", delivery_status="not_attempted")
        report["errors"].append(str(exc))
    except Exception as exc:
        report = report or {"rule": RULE, "state": "unknown", "finding_count": 0, "errors": []}
        report["inspection_status"] = "failed"
        if report.get("delivery_status") == "pending":
            report["delivery_status"] = "unknown"
        else:
            report.setdefault("delivery_status", "not_attempted")
        report["errors"].append("monitor_error:" + type(exc).__name__)
    return public_report(report)
