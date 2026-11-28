"""Exercise one sealed, mock-only clinic booking via the candidate API and its SQS worker."""
import hmac
import json
import os
import re
import time
from datetime import timedelta
from uuid import UUID, uuid4

import requests
from django.core.management.base import CommandError
from django.db import connection, transaction
from django.db.models import Q
from django.utils import timezone

from apps.core.models import OpsAuditLog, Tenant, TenantMembership
from apps.core.models.user import user_internal_username
from apps.core.services.development_qa_ownership import WRONG_NOTE_SETUP_SCHEMA, verify_setup_owner
from apps.core.services.enrollment_notice_development_scenario import cache_state, check_mock, owner_id, require, setup_seal
from apps.core.services.wrong_note_development_scenario import api
from apps.domains.clinic.models import Session, SessionParticipant
from apps.domains.enrollment.models import Enrollment
from apps.domains.messaging.alimtalk_content_builders import get_solapi_template_id
from apps.domains.messaging.models import AutoSendConfig, NotificationLog, ScheduledNotification
from apps.domains.messaging.security import build_recipient_fingerprint, redact_terminal_delivery_payload
from apps.domains.messaging.services.queue_service import build_enqueued_business_key
from apps.domains.students.models import Student


PREFIX = "development.qa.clinic-notice."
ACTOR = "clinic-notice-development-runner"
SCHEMA = "clinic-notice-worker/v1"
PLAN = PREFIX + "plan"
QUEUE = "academy-v1-development-messaging-queue"
TRIGGER = "clinic_reservation_created"
PENDING = "reservation_pending"
APPROVAL = "approval"
ROLES = {PENDING: ("student", "parent", "clinic_teacher"), APPROVAL: ("student", "parent")}
PHONES = {"student": "01098000001", "parent": "01099000001", "clinic_teacher": "01097000001"}
MEMO = "#{예약상태} · #{날짜} #{시간}"


def _actor(tenant):
    return TenantMembership.objects.get(
        tenant=tenant, user__tenant=tenant, user__username=user_internal_username(tenant, "ymath-qa-teacher"),
        user__is_active=True, is_active=True, role="admin",
    ).user


@transaction.atomic
def mark_scope(tenant, release):
    check_mock()
    seal = setup_seal(tenant.code)
    require(seal.target_tenant_id == seal.payload["tenant_id"] == tenant.pk != owner_id()
            and release == os.environ["ACADEMY_DEVELOPMENT_RELEASE_ID"]
            and not OpsAuditLog.objects.filter(action__startswith=PREFIX, target_tenant=tenant).exists()
            and not Session.objects.filter(tenant=tenant).exists() and not SessionParticipant.objects.filter(tenant=tenant).exists()
            and not _outboxes(tenant.pk, []).exists() and not _logs(tenant.pk, [], []).exists(),
            "Expected a new isolated clinic fixture.")
    actor = _actor(tenant)
    student = Student.objects.get(tenant=tenant, user__tenant=tenant, user__is_active=True, deleted_at__isnull=True,
        ps_number="QA-0001", user__username=user_internal_username(tenant, "ymath-qa-student-01"))
    require(actor.phone in (None, "") and student.phone == PHONES["student"] and student.parent_phone == PHONES["parent"]
            and not student.uses_identifier and TenantMembership.objects.filter(
                tenant=tenant, user=student.user, role="student", is_active=True).exists(),
            "Unexpected clinic fixture contacts or accounts.")
    enrollment = Enrollment.objects.get(student=student, tenant=tenant, lecture__tenant=tenant,
        lecture__title="공통수학2 정규반", status="ACTIVE")
    require(tenant.messaging_is_active and AutoSendConfig.objects.filter(tenant=tenant, trigger=TRIGGER, enabled=True,
        message_mode="alimtalk", delay_mode="immediate", template__tenant=tenant, template__body=MEMO).exists(),
        "Expected the isolated clinic reservation template and immediate delivery.")
    actor.phone = PHONES["clinic_teacher"]
    actor.save(update_fields=["phone"])
    request_id = str(uuid4())
    OpsAuditLog.objects.create(action=PREFIX + "scope", actor_username=ACTOR, result="success",
        target_tenant=tenant, summary=request_id, payload=dict(schema=SCHEMA, setup_id=seal.pk, source_id=tenant.pk,
            owner_id=owner_id(), release=release, request_id=request_id, actor_id=actor.pk, teacher_id=actor.pk,
            student_id=student.pk, lecture_id=enrollment.lecture_id, date=str(timezone.localdate() + timedelta(days=1))))


def _scope(tenant_id, seal, release):
    scope = OpsAuditLog.objects.get(action=PREFIX + "scope", target_tenant_id=tenant_id)
    p = scope.payload
    require(scope.actor_username == ACTOR and scope.result == "success"
            and p["schema"] == SCHEMA and seal.target_tenant_id == p["source_id"] == tenant_id
            and p["owner_id"] == owner_id() != tenant_id and p["setup_id"] == seal.pk and p["release"] == release
            and str(UUID(p["request_id"])) == scope.summary == p["request_id"] and p["actor_id"] == p["teacher_id"],
            "Clinic scope changed.")
    actor = _actor(scope.target_tenant)
    require(actor.pk == p["actor_id"] and actor.phone == PHONES["clinic_teacher"]
            and Enrollment.objects.filter(tenant_id=tenant_id, lecture_id=p["lecture_id"], lecture__tenant_id=tenant_id,
                student_id=p["student_id"], student__tenant_id=tenant_id, student__ps_number="QA-0001",
                student__deleted_at__isnull=True, student__user__tenant_id=tenant_id, student__user__is_active=True,
                student__phone=PHONES["student"], student__parent_phone=PHONES["parent"], status="ACTIVE").exists(),
            "Clinic source contact or enrollment changed.")
    return scope


def _session_fields(scope):
    p = scope.payload
    title = f"QA clinic {scope.summary}"
    return dict(title=title, location=title, date=p["date"], start_time="13:00:00", duration_minutes=240,
                max_participants=4, booking_mode="time_range", booking_interval_minutes=30,
                booking_max_stay_minutes=120, notification_teacher_id=p["teacher_id"],
                target_lecture_ids=[p["lecture_id"]])


def _session(scope):
    tid = scope.target_tenant_id
    require(not Session.objects.exclude(tenant_id=tid).filter(
        Q(notification_teacher__tenant_id=tid) | Q(created_by__tenant_id=tid) | Q(target_lectures__tenant_id=tid)
    ).exists(), "A foreign clinic session references the disposable source.")
    rows = list(Session.objects.filter(tenant_id=tid))
    if not rows:
        return None
    require(len(rows) == 1, "Unexpected additional clinic session.")
    session = rows[0]
    fields = _session_fields(scope)
    lectures = fields.pop("target_lecture_ids")
    require(session.created_by_id == scope.payload["actor_id"]
            and list(session.target_lectures.values_list("id", flat=True)) == lectures
            and all(str(getattr(session, k)) == str(v) for k, v in fields.items()), "Unexpected clinic session.")
    return session


def _plan_fields(scope, session):
    p = scope.payload
    return dict(schema=SCHEMA, setup_id=p["setup_id"], tenant_id=scope.target_tenant_id, release=p["release"],
                request_id=scope.summary, actor_id=p["actor_id"], teacher_id=p["teacher_id"], student_id=p["student_id"],
                lecture_id=p["lecture_id"], session_id=session.pk, redelivery_role="clinic_teacher")


def _participant(scope, plan, session):
    tid = scope.target_tenant_id
    rows = list(SessionParticipant.objects.filter(Q(tenant_id=tid) | Q(session__tenant_id=tid) | Q(student__tenant_id=tid)
        | Q(enrollment__tenant_id=tid) | Q(status_changed_by__tenant_id=tid) | Q(checked_out_by__tenant_id=tid)
        | Q(completed_by__tenant_id=tid)))
    if plan:
        require(session and plan.payload == _plan_fields(scope, session) and plan.summary == scope.summary
                and plan.actor_username == ACTOR and plan.result == "success" and plan.target_tenant_id == tid,
                "Clinic plan differs from its sealed session.")
    if not rows:
        return None
    require(plan and len(rows) == 1, "Unexpected or unbound clinic participant.")
    part = rows[0]
    require(part.tenant_id == tid and part.session_id == session.pk and part.student_id == plan.payload["student_id"]
            and part.source == "student_request" and part.status in ("pending", "booked") and part.requested_after_cutoff
            and part.created_at >= plan.created_at
            and (not part.enrollment_id or Enrollment.objects.filter(pk=part.enrollment_id, tenant_id=tid,
                                                                      student_id=part.student_id).exists())
            and (part.status != "booked" or part.status_changed_by_id == plan.payload["actor_id"])
            and str(part.booking_start_time) == "14:30:00" and str(part.booking_end_time) == "15:30:00",
            "Persisted reservation differs from the sealed request.")
    require(not part.plan_items.exists() and not part.checked_in_at and not part.checked_out_at and not part.completed_at,
            "The clinic booking contains additional staff work.")
    return part


def _login(http, tenant, name, password):
    http.trust_env = False
    http.headers["X-Tenant-Code"] = tenant.code
    token = api(http, "POST", "token/", json={"username": name, "password": password})
    http.headers["Authorization"] = f"Bearer {token['access']}"
    return api(http, "GET", "core/me/")["id"]


def run(tenant, password, *, release, production_database):
    from apps.core.management.commands.verify_development_wrong_notes import check_boundary
    check_boundary(release, production_database)
    check_mock()
    scope = _scope(tenant.pk, setup_seal(tenant.code), release)
    p = scope.payload
    require(not _session(scope) and not OpsAuditLog.objects.filter(action=PLAN, target_tenant=tenant).exists(),
            "Clinic scenario already started. Use cleanup-only recovery.")
    with requests.Session() as admin, requests.Session() as student:
        require(_login(admin, tenant, "ymath-qa-teacher", password) == p["actor_id"]
                and Student.objects.filter(pk=p["student_id"], tenant=tenant,
                    user_id=_login(student, tenant, "ymath-qa-student-01", password)).exists(), "Clinic login mismatch.")
        choices = api(admin, "GET", "clinic/sessions/notification-teachers/")
        require(p["teacher_id"] in [r["id"] for r in choices], "Assigned synthetic teacher is unavailable.")
        new = api(admin, "POST", "clinic/sessions/", 201, json=_session_fields(scope))
        session = _session(scope)
        require(session and new["id"] == session.pk and new["notification_teacher_id"] == p["teacher_id"]
                and "notification_teacher_id" not in api(student, "GET", f"clinic/sessions/{session.pk}/"),
                "Clinic session assignment or privacy boundary changed.")
        policy = dict(booking_cutoff_mode="before_start", booking_cutoff_minutes=10080, auto_approve_booking=True,
                      allow_late_requests=False)
        api(admin, "PATCH", "clinic/settings/", json=policy)
        plan = OpsAuditLog.objects.create(action=PLAN, actor_username=ACTOR, result="success", target_tenant=tenant,
                                          summary=scope.summary, payload=_plan_fields(scope, session))
        reserve = "clinic/participants/bulk-create/"
        body = dict(session_ids=[session.pk], booking_start_time="14:30", booking_end_time="15:30")
        denied = api(student, "POST", reserve, 400, json=body)
        require("booking_cutoff" in denied and not _participant(scope, plan, session), "Closed deadline admitted a request.")
        api(admin, "PATCH", "clinic/settings/", json={"allow_late_requests": True})
        saved = api(admin, "GET", "clinic/settings/")
        require(all(saved.get(k) == v for k, v in {**policy, "allow_late_requests": True}.items()),
                "Clinic cutoff policy did not persist.")
        result = api(student, "POST", reserve, 201, json=body)
        api(student, "POST", reserve, 409, json=body)
        part = _participant(scope, plan, session)
        require(part and result["count"] == 1 and [r["id"] for r in result["participants"]] == [part.pk]
                and part.status == "pending", "Late request failed to enter approval.")
        path = f"clinic/participants/{part.pk}/"
        for phase, status in ((PENDING, "pending"), (APPROVAL, "booked")):
            if phase == APPROVAL:
                api(admin, "PATCH", path + "set_status/", json={"status": "booked"})
                api(admin, "PATCH", path + "set_status/", 400, json={"status": "booked"})
            for http in (student, admin):
                detail = api(http, "GET", path)
                require(detail["id"] == part.pk and detail["status"] == status
                        and detail["requested_after_cutoff"] is True, "Booking status did not persist across roles.")
            part = _participant(scope, plan, session)
            require(part.status == status, "Clinic reservation changed while checking receipts.")
            evidence = _await_evidence(scope, plan, part)
        return dict(request_id=scope.summary, setup_id=p["setup_id"], source_tenant_id=tenant.pk,
                    owner_tenant_id=p["owner_id"], participant_id=part.pk, deliveries=evidence["deliveries"],
                    acknowledged=5, redelivered=1, mock_calls=5)

def _events(source_id, request_id, plan_id):
    return OpsAuditLog.objects.filter(action__startswith=PREFIX).exclude(
        action__in=[PREFIX + "scope", PLAN, PREFIX + "cleanup"]).filter(
        Q(target_tenant_id=source_id) | Q(summary=request_id) | Q(payload__source_tenant_id=source_id)
        | Q(payload__plan_id=plan_id))


def _outboxes(source_id, origins):
    return ScheduledNotification.objects.filter(
        Q(tenant_id=source_id) | Q(payload__source_tenant_id=source_id) | Q(origin_id__in=origins))


def _logs(source_id, origins, keys):
    return NotificationLog.objects.filter(Q(source_tenant_id=source_id) | Q(tenant_id=source_id)
                                          | Q(origin_id__in=origins) | Q(business_idempotency_key__in=keys))


def _worker_events(scope, plan, part, phase, role, box, log, events):
    forced = phase == PENDING and role == "clinic_teacher"
    expected = {"mock-send", "redelivery", "ack"} if forced else {"mock-send", "ack"}
    kinds = [event.action.removeprefix(PREFIX) for event in events]
    require(len(set(kinds)) == len(kinds) and set(kinds) <= expected, "Duplicate or unexpected clinic worker event.")
    p = plan.payload
    binding = dict(schema=SCHEMA, setup_id=p["setup_id"], plan_id=plan.pk, release=p["release"], request_id=scope.summary,
                   actor_id=p["actor_id"], source_tenant_id=scope.target_tenant_id, owner_tenant_id=scope.payload["owner_id"],
                   participant_id=part.pk, session_id=p["session_id"], student_id=p["student_id"], teacher_id=p["teacher_id"],
                   origin_id=box.origin_id, role=role, business_key=box.business_idempotency_key, outbox_id=box.pk,
                   log_id=log.pk, message_id=log.sqs_message_id, queue=QUEUE)
    receipt = None
    for kind, event in zip(kinds, events):
        d = event.payload
        require(event.result == "success" and event.actor_username == "MESSAGING"
                and event.target_tenant_id == scope.target_tenant_id and event.summary == scope.summary
                and all(d.get(k) == v for k, v in binding.items()), "Clinic worker receipt ownership changed.")
        redelivered = kind == "ack" and forced
        require(type(d.get("receive_count")) is int and d["receive_count"] == (2 if redelivered else 1),
                "Unexpected clinic SQS receive count.")
        if kind == "mock-send":
            require(d.get("provider_message_id") == log.provider_message_id, "Mock provider receipt changed.")
        if kind == "ack":
            require(d.get("deduplicated") is forced, "Clinic redelivery did not preserve the mock provider effect.")
            receipt = dict(phase=phase, role=role, target_id=int(log.target_id), outbox_id=box.pk,
                           business_key=box.business_idempotency_key, log_id=log.pk, provider_message_id=log.provider_message_id,
                           message_id=log.sqs_message_id, ack_id=event.pk, receive_count=d["receive_count"], deduplicated=forced)
    if set(kinds) != expected:
        return None
    event_ids = dict(zip(kinds, (e.pk for e in events)))
    receipt.update(mock_event_id=event_ids["mock-send"], redelivery_event_id=event_ids.get("redelivery"))
    return receipt


def worker_evidence(scope, plan, part):
    """Read the actual owner log and successful consumer ACK for every expected source outbox."""
    tid, owner = scope.target_tenant_id, scope.payload["owner_id"]
    phases = (PENDING, APPROVAL) if part.status == "booked" else (PENDING,)
    origins = {f"clinic_participant:{part.pk}:{phase}": phase for phase in phases}
    # Include the approval origin even while pending, to catch an unowned/cross-tenant effect.
    all_origins = [f"clinic_participant:{part.pk}:{phase}" for phase in ROLES]
    expected = {(origin, role) for origin, phase in origins.items() for role in ROLES[phase]}
    boxes = list(_outboxes(tid, all_origins))
    keyed = {(box.origin_id, box.payload.get("target_type")): box for box in boxes}
    require(len(keyed) == len(boxes) and keyed.keys() <= expected, "Duplicate or foreign clinic outbox.")
    keys = [box.business_idempotency_key for box in boxes]
    logs = list(_logs(tid, all_origins, keys))
    by_key = {log.business_idempotency_key: log for log in logs}
    require(len(set(keys)) == len(keys) and len(by_key) == len(logs) and by_key.keys() <= set(keys),
            "Unexpected clinic owner/source log.")
    events = list(_events(tid, scope.summary, plan.pk))
    require(all((e.payload.get("origin_id"), e.payload.get("role")) in expected for e in events),
            "Unexpected clinic worker delivery.")
    inv = dict(owner_id=owner, outboxes=[], logs=[], events=[], messages=[], deliveries=[])
    for (origin, role), box in keyed.items():
        phase = origins[origin]
        target = plan.payload["teacher_id"] if role == "clinic_teacher" else part.student_id
        payload = box.payload
        fingerprint = build_recipient_fingerprint(PHONES[role])
        template_id = get_solapi_template_id(TRIGGER)
        # Admission erases the raw number when the outbox becomes terminal. Recompute the
        # business key with the sealed synthetic contact and compare its independent HMAC.
        if box.status == "sent":
            require(payload == redact_terminal_delivery_payload(trigger=TRIGGER, payload=payload),
                    "A submitted clinic outbox retained unsealed payload data.")
        contact_ok = "to" not in payload if payload.get("redacted") is True else payload.get("to") == PHONES[role]
        require(box.tenant_id == tid and box.trigger == TRIGGER and box.origin_type == f"clinic.{TRIGGER}"
                and payload.get("domain_object_id") == payload.get("origin_id") == origin
                and payload.get("tenant_id") == owner and payload.get("source_tenant_id") == tid
                and str(payload.get("target_id")) == str(target) and contact_ok
                and payload.get("event_type") == TRIGGER and payload.get("message_mode") == "alimtalk"
                and payload.get("occurrence_key") == f"{TRIGGER}:{origin}"
                and template_id and payload.get("template_id") == template_id
                and fingerprint and payload.get("recipient_fingerprint") == box.recipient_fingerprint == fingerprint
                and box.business_idempotency_key == build_enqueued_business_key(tenant_id=tid, payload={**payload, "to": PHONES[role]}),
                "Clinic outbox differs from the sealed source, recipient or approved template.")
        log = by_key.get(box.business_idempotency_key)
        if box.status != "sent" or log is None or log.status != "sent":
            continue
        memo_state = "마감 후 승인 대기" if phase == PENDING else "예약 확정"
        memo = f"{memo_state} · {scope.payload['date']} 14:30–15:30"
        require(log.success and log.tenant_id == owner and log.source_tenant_id == tid and log.notification_type == TRIGGER
                and log.origin_type == box.origin_type and log.origin_id == origin and log.message_mode == "alimtalk"
                and log.target_type == role and log.target_id == str(target) and log.recipient_fingerprint == fingerprint
                and memo in log.message_body and re.fullmatch(r"mock-[0-9a-f]{12}", log.provider_message_id)
                and str(UUID(log.sqs_message_id)) == log.sqs_message_id, "Clinic worker log identity or body changed.")
        group = [e for e in events if e.payload.get("origin_id") == origin and e.payload.get("role") == role]
        receipt = _worker_events(scope, plan, part, phase, role, box, log, group)
        if receipt is not None:
            inv["outboxes"].append(box.pk)
            inv["logs"].append(log.pk)
            inv["events"].extend(e.pk for e in group)
            inv["messages"].append(log.sqs_message_id)
            inv["deliveries"].append(receipt)
    if len(inv["deliveries"]) != len(expected):
        return None
    require(len(set(inv["messages"])) == len(expected)
            and len({r["provider_message_id"] for r in inv["deliveries"]}) == len(expected)
            and len(set(inv["events"])) == len(events),
            "Clinic receipts share a queue message or contain unbound worker events.")
    return inv


def _await_evidence(scope, plan, part):
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        evidence = worker_evidence(scope, plan, part)
        if evidence:
            redis, locks, _ = cache_state(evidence)
            if not redis.exists(*locks):
                return evidence
        time.sleep(2)
    raise CommandError("Clinic worker receipts or message locks remain unsettled; preserve the scope.")


def _snapshot_signature(inventory, capability):
    # On recovery the source and its worker rows are already gone. Bind the exact
    # Redis message IDs to the original capability before those rows can disappear.
    data = json.dumps(inventory, sort_keys=True, separators=(",", ":")).encode()
    return hmac.digest(capability.encode(), b"clinic-notice-cleanup/v1\0" + data, "sha256").hex()


def cleanup(code, capability):
    """Keep the fixture on uncertainty. Pin physical owner rows before the source FK is removed."""
    from apps.core.management.commands.verify_development_wrong_notes import cleanup as cleanup_fixture
    check_mock()
    release = os.environ["ACADEMY_DEVELOPMENT_RELEASE_ID"]
    seal = setup_seal(code)
    tid = seal.payload["tenant_id"]
    verify_setup_owner(code, tid, capability, seal.payload, schema=WRONG_NOTE_SETUP_SCHEMA)
    with transaction.atomic():
        seal = OpsAuditLog.objects.select_for_update().get(pk=seal.pk)
        snapshots = OpsAuditLog.objects.filter(action=PREFIX + "cleanup", payload__setup_id=seal.pk)
        if snapshots.exists():
            snapshot = snapshots.get()
            inv = snapshot.payload
            require(hmac.compare_digest(snapshot.summary, _snapshot_signature(inv, capability))
                    and snapshot.target_tenant_id is None and snapshot.actor_username == ACTOR
                    and snapshot.result == "success" and inv["schema"] == SCHEMA
                    and inv["release"] == release and inv["source_id"] == tid and inv["owner_id"] == owner_id() != tid
                    and not Tenant.objects.filter(Q(pk=tid) | Q(code__iexact=code)).exists(),
                    "Clinic cleanup snapshot and current ownership disagree.")
        else:
            source = Tenant.objects.select_for_update().get(pk=tid, code=code)
            scope = _scope(tid, seal, release)
            plans = list(OpsAuditLog.objects.select_for_update().filter(action=PLAN).filter(
                Q(target_tenant=source) | Q(summary=scope.summary) | Q(payload__setup_id=seal.pk)))
            require(len(plans) <= 1, "Unexpected additional clinic plan.")
            plan = plans[0] if plans else None
            # Serialize against admission or approval while checking the finished phase.
            list(Session.objects.select_for_update().filter(tenant=source).values_list("pk", flat=True))
            list(SessionParticipant.objects.select_for_update().filter(tenant=source).values_list("pk", flat=True))
            session = _session(scope)
            part = _participant(scope, plan, session)
            if part:
                inv = worker_evidence(scope, plan, part)
                require(inv is not None, "Clinic work is unsettled. Preserve the fixture for cleanup-only recovery.")
                redis, locks, _ = cache_state(inv)
                require(not redis.exists(*locks), "A clinic delivery still has a worker lock.")
                rows = NotificationLog.objects.select_for_update().filter(
                    tenant_id=scope.payload["owner_id"], source_tenant_id=tid, pk__in=inv["logs"])
                require(set(rows.values_list("pk", flat=True)) == set(inv["logs"]), "Common owner rows changed.")
                boxes = ScheduledNotification.objects.select_for_update().filter(
                    tenant_id=tid, trigger=TRIGGER, status="sent", pk__in=inv["outboxes"])
                require(set(boxes.values_list("pk", flat=True)) == set(inv["outboxes"]), "Clinic outboxes changed.")
                rows.delete()
                boxes.delete()
                OpsAuditLog.objects.filter(pk__in=inv["events"], target_tenant_id=tid).delete()
                inv.pop("deliveries")
            else:
                require(not _outboxes(tid, []).exists() and not _logs(tid, [], []).exists()
                        and not _events(tid, scope.summary, plan.pk if plan else 0).exists(), "Unbound clinic delivery work.")
                inv = dict(owner_id=owner_id(), outboxes=[], logs=[], events=[], messages=[])
            inv.update(schema=SCHEMA, source_id=tid, release=release, setup_id=seal.pk, request_id=scope.summary,
                       scope_id=scope.pk, plan_id=plan.pk if plan else 0, session_id=session.pk if session else 0,
                       participant_id=part.pk if part else 0)
            OpsAuditLog.objects.filter(pk__in=[inv["scope_id"], inv["plan_id"]], target_tenant_id=tid).delete()
            OpsAuditLog.objects.create(action=PREFIX + "cleanup", actor_username=ACTOR, result="success",
                                       summary=_snapshot_signature(inv, capability), payload=inv)
            cleanup_fixture(code, capability)
    require(not connection.in_atomic_block, "Clinic residue requires committed reads.")
    residue = cleanup_fixture(code, capability)
    cache_remaining = 0
    if inv["messages"]:
        redis, locks, progress = cache_state(inv)
        require(not redis.exists(*locks), "Clinic worker reacquired an acknowledged message.")
        redis.delete(*progress)
        cache_remaining = redis.exists(*locks, *progress)
    origins = [f"clinic_participant:{inv['participant_id']}:{phase}" for phase in ROLES] if inv["participant_id"] else []
    residue.update(
        clinic_sessions=Session.objects.filter(Q(tenant_id=tid) | Q(pk=inv["session_id"])).count(),
        clinic_participants=SessionParticipant.objects.filter(Q(tenant_id=tid) | Q(pk=inv["participant_id"])).count(),
        notice_outboxes=(_outboxes(tid, origins) | ScheduledNotification.objects.filter(pk__in=inv["outboxes"])).count(),
        owner_notice_logs=(_logs(tid, origins, []) | NotificationLog.objects.filter(pk__in=inv["logs"])).count(),
        worker_evidence=(_events(tid, inv["request_id"], inv["plan_id"]) | OpsAuditLog.objects.filter(
            pk__in=[*inv["events"], inv["scope_id"], inv["plan_id"]])).count(),
        messaging_redis=cache_remaining,
    )
    require(not any(residue.values()), "Clinic messaging cleanup did not reach zero.")
    return residue
