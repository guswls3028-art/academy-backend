"""A mock-only enrollment through the candidate HTTP API and its separate SQS consumer."""
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from uuid import UUID, uuid4

import requests
from django.conf import settings
from django.core.management.base import CommandError
from django.db import connection, transaction
from django.db.models import Q

from apps.core.management.commands.setup_ymath_realuse_scenario import DEVELOPMENT_MESSAGING_OWNER_CODE
from apps.core.models import OpsAuditLog, Tenant, TenantMembership
from apps.core.services.development_qa_ownership import WRONG_NOTE_SETUP_SCHEMA, verify_setup_owner
from apps.domains.enrollment.models import EnrollmentNoticeRecipient, EnrollmentNoticeRegistration as Registration
from apps.domains.messaging.models import NotificationLog, ScheduledNotification
from apps.domains.messaging.policy import get_owner_tenant_id
from apps.domains.students.models import Student


PREFIX = "development.qa.enrollment-notice."
PLAN = PREFIX + "plan"
EVENTS = [PREFIX + event for event in ("mock-send", "redelivery", "ack")]
ACTOR = "enrollment-notice-development-runner"
SCHEMA = "enrollment-notice-worker/v1"


def require(value, message):
    if not value:
        raise CommandError(message)


def check_mock():
    require(os.environ.get("ACADEMY_RUNTIME_ENV") == "development"
            and os.environ.get("SOLAPI_MOCK", "").lower() == "true"
            and os.environ.get("SOLAPI_KAKAO_PF_ID") == "development-mock-pfid"
            and os.environ.get("MESSAGING_DRY_RUN_TRIGGERS", "").strip() == ""
            and settings.MESSAGING_SQS_QUEUE_NAME == "academy-v1-development-messaging-queue"
            and connection.settings_dict["NAME"] == "academy_api_development"
            and connection.settings_dict["USER"] == "academy_api_development_app",
            "Enrollment QA requires the candidate's isolated mock messaging environment.")


def owner_id():
    return Tenant.objects.only("pk").get(
        pk=get_owner_tenant_id(), code=DEVELOPMENT_MESSAGING_OWNER_CODE, is_active=True,
    ).pk


def setup_seal(code):
    require(re.fullmatch(r"qa-ymath-realuse-wn-\d+-\d+-[a-f0-9]{12}", code), "Unexpected fixture code.")
    return OpsAuditLog.objects.get(action="development.wrong-note.setup", actor_username="wrong-note-development-runner",
                                   result="success", payload__tenant_code=code)


def mark_scope(tenant, release):
    seal = setup_seal(tenant.code)
    OpsAuditLog.objects.create(action=PREFIX + "scope", actor_username=ACTOR, target_tenant=tenant,
                               payload={"setup_id": seal.pk, "source_id": tenant.pk, "release": release},
                               result="success")


def batch_for(plan):
    p = plan.payload
    batch = Registration.objects.filter(tenant_id=p["tenant_id"], actor_id=p["actor_id"], request_id=p["request_id"],
                                        lecture_id=p["lecture_id"]).get()
    require(sorted(batch.student_ids) == sorted(p["student_ids"]) and batch.student_notice == "send"
            and batch.parent_notice == "skip", "Registration differs from its sealed choices.")
    return batch


def run_scheduler(plan_id, release, production_database):
    # The release harness launches this short-lived candidate API process before POST.
    # Each tick consumes only the two sealed students, never other development tenants.
    from apps.core.management.commands.verify_development_wrong_notes import check_boundary
    from apps.domains.messaging.scheduled import process_due_notifications
    from apps.support.enrollment.notice_dispatch import process_registration_notices

    check_boundary(release, production_database)
    check_mock()
    plan = OpsAuditLog.objects.get(pk=plan_id, action=PLAN, actor_username=ACTOR, result="success")
    p = plan.payload
    seal = setup_seal(plan.target_tenant.code)
    require(p["release"] == release and p["schema"] == SCHEMA and p["setup_id"] == seal.pk
            and seal.target_tenant_id == plan.target_tenant_id == p["tenant_id"] != owner_id()
            and p["request_id"] == plan.summary and len(set(p["student_ids"])) == 2, "Invalid scheduler scope.")
    for _ in range(75):
        try:
            batch = batch_for(plan)
        except Registration.DoesNotExist:
            time.sleep(2)
            continue
        process_registration_notices(limit=4, registration=batch)
        boxes = list(batch.recipients.exclude(outbox_id=None).values_list("outbox_id", flat=True))
        if boxes:
            bound = list(ScheduledNotification.objects.filter(
                pk__in=boxes, tenant_id=plan.target_tenant_id, trigger="registration_approved_student",
            ).values_list("pk", flat=True))
            require(set(bound) == set(boxes), "Foreign scheduled notification.")
            process_due_notifications(notification_ids=bound, batch_size=2)
            if len(bound) == 2 and ScheduledNotification.objects.filter(pk__in=bound, status="sent").count() == 2:
                return
        time.sleep(2)
    raise CommandError("Development scheduled admissions did not complete.")


def run(tenant, password, *, release, production_database):
    from apps.core.services.wrong_note_development_scenario import api
    check_mock()
    require(tenant.pk != owner_id(), "Expected a separate source tenant.")
    seal = setup_seal(tenant.code)
    require(seal.target_tenant_id == tenant.pk, "Fixture seal mismatch.")
    students = list(Student.objects.filter(tenant=tenant, deleted_at__isnull=True).order_by("ps_number")
                    .values_list("pk", "ps_number", "phone", "uses_identifier"))
    require([(number, phone, identifier) for _, number, phone, identifier in students] == [
        (f"QA-{i:04d}", f"01098{i:06d}", False) for i in (1, 2)], "Expected the two synthetic contacts.")
    ids = [row[0] for row in students]
    with requests.Session() as http:
        http.trust_env = False
        http.headers["X-Tenant-Code"] = tenant.code
        token = api(http, "POST", "token/", json={"username": "ymath-qa-teacher", "password": password})
        http.headers["Authorization"] = f"Bearer {token['access']}"
        actor = api(http, "GET", "core/me/")["id"]
        require(TenantMembership.objects.filter(tenant=tenant, user_id=actor, role="admin", is_active=True).exists(),
                "Expected the scenario administrator.")
        request_id = str(uuid4())
        title = f"QA notice {request_id}"
        lecture = api(http, "POST", "lectures/lectures/", 201, json={"name": title, "title": title, "subject": "MATH"})["id"]
        plan = OpsAuditLog.objects.create(
            action=PLAN, actor_username=ACTOR, target_tenant=tenant, summary=request_id, result="success",
            payload=dict(schema=SCHEMA, setup_id=seal.pk, tenant_id=tenant.pk, release=release, request_id=request_id,
                         actor_id=actor, lecture_id=lecture, student_ids=ids, redelivery_student_id=ids[0]),
        )
        args = [sys.executable, str(Path(__file__).resolve().parents[3] / "manage.py"), "verify_development_wrong_notes",
                "--release", release, "--production-database", production_database, "--enrollment-tick", str(plan.pk)]
        child = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            body = dict(lecture=lecture, students=ids, request_id=request_id, student_notice="send", parent_notice="skip")
            for _ in range(2):
                result = api(http, "POST", "enrollments/register-with-notices/", 201, json=body)
                require(result["request_id"] == request_id, "Registration replay returned a different receipt.")
            batch = batch_for(plan)
            require(batch.actor_id == actor, "Registration actor mismatch.")
            for _ in range(90):
                require(child.poll() in (None, 0), "Development scheduled admission failed.")
                result = api(http, "GET", "enrollments/registration-notices/",
                             params={"lecture": lecture, "request_id": request_id})
                rows = result["students"]
                require(result["request_id"] == request_id and result["lecture"] == lecture
                        and {r["id"] for r in rows} == set(ids)
                        and all(r["parent"] == {"status": "skipped", "log_id": None} for r in rows),
                        "Saved recipient choices or registration identity changed.")
                if child.poll() == 0 and all(r["student"]["status"] == "accepted" for r in rows):
                    evidence = worker_evidence(plan, batch)
                    if evidence is not None:
                        redis, locks, _ = cache_state(evidence)
                        if redis.exists(*locks):
                            time.sleep(2)
                            continue
                        require(set(evidence["logs"]) == {r["student"]["log_id"] for r in rows},
                                "Product and worker receipts differ.")
                        checked = api(http, "POST", "enrollments/verify-registration-notice/", json={
                            "lecture": lecture, "request_id": request_id, "log_id": evidence["logs"][0]})
                        require(checked["provider_delivery_status"] == "unavailable", "Unexpected mock carrier status.")
                        return {"scheduled_students": 2, "acknowledged": 2, "redelivered": 1,
                                "mock_calls": 2, "request_id": request_id, "registration_id": batch.pk,
                                "setup_id": seal.pk, "source_tenant_id": tenant.pk,
                                "owner_tenant_id": evidence["owner_id"], "deliveries": evidence["deliveries"]}
                time.sleep(2)
            raise CommandError("Timed out waiting for enrollment worker receipts.")
        finally:
            if child.poll() is None:
                child.terminate()
            child.wait(timeout=15)


def worker_events(plan):
    return OpsAuditLog.objects.filter(action__in=EVENTS, payload__plan_id=plan.pk)


def worker_evidence(plan, batch):
    """Return identifiers only when each persisted provider call and actual queue ACK matches."""
    tenant = batch.tenant_id
    owner = owner_id()
    p = plan.payload
    people = list(batch.recipients.order_by("student_id", "role"))
    require({(r.student_id, r.role) for r in people} == {(s, role) for s in p["student_ids"]
            for role in ("student", "parent")} and len(people) == 4, "Unexpected recipient set.")
    evidence = {"logs": [], "outboxes": [], "messages": [], "events": [], "deliveries": [],
                "recipients": [r.pk for r in people],
                "owner_id": owner, "registration_id": batch.pk}
    for r in people:
        if r.role == "parent":
            require(r.status == "skipped" and not r.outbox_id and not r.business_key, "Skipped parent has work.")
            continue
        if r.status != "admitted":
            return None
        outbox = ScheduledNotification.objects.get(pk=r.outbox_id, tenant_id=tenant,
            business_idempotency_key=r.business_key, trigger="registration_approved_student")
        require(outbox.payload.get("domain_object_id") == f"enrollment-notice:{r.pk}"
                and outbox.payload.get("tenant_id") == owner and outbox.payload.get("source_tenant_id") == tenant
                and outbox.payload.get("target_id") == f"student:{r.student_id}", "Outbox ownership mismatch.")
        if outbox.status != "sent":
            return None
        log = NotificationLog.objects.filter(tenant_id=owner, source_tenant_id=tenant,
            message_mode="alimtalk", target_type="account", target_id=f"student:{r.student_id}",
            business_idempotency_key=r.business_key, recipient_fingerprint=r.recipient_fingerprint).first()
        if not log or not log.success or log.status != "sent":
            return None
        require(re.fullmatch(r"mock-[0-9a-f]{12}", log.provider_message_id)
                and str(UUID(log.sqs_message_id)) == log.sqs_message_id, "Expected mock and SQS identities.")
        target = r.student_id == p["redelivery_student_id"]
        expected = {"mock-send", "ack", "redelivery"} if target else {"mock-send", "ack"}
        events = list(worker_events(plan).filter(payload__recipient_id=r.pk))
        kinds = [event.action.removeprefix(PREFIX) for event in events]
        require(set(kinds) <= expected and len(kinds) == len(set(kinds)), "Duplicate or unexpected worker side effect.")
        if set(kinds) != expected:
            return None
        binding = dict(schema=SCHEMA, setup_id=p["setup_id"], plan_id=plan.pk, release=p["release"],
                       request_id=p["request_id"], actor_id=batch.actor_id, source_tenant_id=tenant, owner_tenant_id=owner,
                       recipient_id=r.pk, student_id=r.student_id, role=r.role, business_key=r.business_key,
                       log_id=log.pk, message_id=log.sqs_message_id, queue="academy-v1-development-messaging-queue")
        for event in events:
            d = event.payload
            require(event.actor_username == "MESSAGING" and event.result == "success"
                    and event.summary == p["request_id"] and event.target_tenant_id == tenant
                    and all(d.get(k) == v for k, v in binding.items()), "Worker evidence ownership mismatch.")
            is_ack = event.action == PREFIX + "ack"
            count = d.get("receive_count")
            require(type(count) is int and (count >= 2 if is_ack and target else count == 1),
                    "Unexpected SQS receive count.")
            if is_ack:
                require(d.get("deduplicated") is target, "Missing durable redelivery decision.")
                evidence["deliveries"].append(dict(recipient_id=r.pk, role=r.role, student_id=r.student_id,
                    outbox_id=outbox.pk, business_key=r.business_key, log_id=log.pk, message_id=log.sqs_message_id,
                    ack_id=event.pk, receive_count=count, deduplicated=target))
            if event.action == PREFIX + "mock-send":
                require(d.get("provider_message_id") == log.provider_message_id, "Mock provider identity mismatch.")
        evidence["logs"].append(log.pk)
        evidence["outboxes"].append(outbox.pk)
        evidence["messages"].append(log.sqs_message_id)
        evidence["events"].extend(e.pk for e in events)
    require(len(set(evidence["messages"])) == 2
            and set(ScheduledNotification.objects.filter(tenant_id=tenant).values_list("pk", flat=True))
            == set(evidence["outboxes"])
            and set(worker_events(plan).values_list("pk", flat=True)) == set(evidence["events"])
            and set(NotificationLog.objects.filter(source_tenant_id=tenant).values_list("pk", flat=True))
            == set(evidence["logs"]), "Unexpected source messaging records.")
    return evidence


def cache_state(inv):
    from libs.redis.client import get_redis_client
    redis = get_redis_client()
    require(redis is not None and redis.ping(), "Messaging cache readback unavailable.")
    locks = [f"job:messaging:{m}:lock" for m in inv["messages"]]
    progress = [f"tenant:{inv['owner_id']}:job:messaging:{m}:progress" for m in inv["messages"]]
    return redis, locks, progress


def prune_completed(plan, batch):
    tid = batch.tenant_id
    people = list(batch.recipients.select_for_update().order_by("pk"))
    if len(people) == 4 and all(r.status in {"queued", "failed", "no_recipient", "skipped"}
                                and not r.business_key and r.outbox_id is None for r in people):
        # The admission transaction locks the same recipient before preparing an outbox.
        # A failed or cancelled rehearsal that never reached SQS can be cleaned safely.
        require(not ScheduledNotification.objects.filter(tenant_id=tid).exists()
                and not NotificationLog.objects.filter(source_tenant_id=tid).exists()
                and not worker_events(plan).exists(), "Unexpected work before admission.")
        return dict(owner_id=owner_id(), messages=[], logs=[], outboxes=[], events=[],
                    recipients=[r.pk for r in people], registration_id=batch.pk)
    inv = worker_evidence(plan, batch)
    require(inv is not None, "Unsettled enrollment delivery. Preserve the owned fixture.")
    redis, locks, progress = cache_state(inv)
    require(not redis.exists(*locks), "Worker still owns a message lock.")
    exact = NotificationLog.objects.select_for_update().filter(
        pk__in=inv["logs"], tenant_id=inv["owner_id"], source_tenant_id=tid, status="sent", success=True,
        business_idempotency_key__in=batch.recipients.filter(role="student").values("business_key"))
    require(sorted(exact.values_list("pk", flat=True)) == sorted(inv["logs"]), "Owner rows changed.")
    exact.delete()
    redis.delete(*progress)
    worker_events(plan).filter(pk__in=inv["events"]).delete()
    return inv


def cleanup(code, capability):
    """Retain recoverable ownership if jobs are in flight or any source/owner identity differs."""
    from apps.core.management.commands.verify_development_wrong_notes import cleanup as cleanup_fixture
    check_mock()
    seal = setup_seal(code)
    tid = seal.payload["tenant_id"]
    verify_setup_owner(code, tid, capability, seal.payload, schema=WRONG_NOTE_SETUP_SCHEMA)
    with transaction.atomic():
        # Serialize recovery with itself. The independent inventory survives source FK deletion.
        seal = OpsAuditLog.objects.select_for_update().get(pk=seal.pk)
        snapshots = OpsAuditLog.objects.filter(action=PREFIX + "cleanup", actor_username=ACTOR,
                                                payload__setup_id=seal.pk, result="success")
        if snapshots.exists():
            inv = snapshots.get().payload
            require(inv["source_id"] == tid and inv["owner_id"] == owner_id()
                    and inv["release"] == os.environ["ACADEMY_DEVELOPMENT_RELEASE_ID"]
                    and not Tenant.objects.filter(pk=tid).exists(), "Inconsistent cleanup snapshot.")
        else:
            require(Tenant.objects.select_for_update().get(pk=tid, code=code).pk != owner_id(),
                    "Unexpected shared owner in fixture cleanup.")
            mark = OpsAuditLog.objects.select_for_update().get(action=PREFIX + "scope", actor_username=ACTOR,
                target_tenant_id=tid, result="success", payload__source_id=tid, payload__setup_id=seal.pk,
                payload__release=os.environ["ACADEMY_DEVELOPMENT_RELEASE_ID"])
            plans = OpsAuditLog.objects.filter(action=PLAN, target_tenant_id=tid, payload__setup_id=seal.pk,
                                               result="success", actor_username=ACTOR)
            if plans.exists() and Registration.objects.filter(tenant_id=tid).exists():
                plan = plans.get()
                batch = batch_for(plan)
                require(Registration.objects.filter(tenant_id=tid).count() == 1, "Unexpected registration batch.")
                inv = prune_completed(plan, batch)
                inv["plan_id"] = plan.pk
                plan.delete()
            else:
                require(not Registration.objects.filter(tenant_id=tid).exists()
                        and not NotificationLog.objects.filter(source_tenant_id=tid).exists()
                        and not ScheduledNotification.objects.filter(tenant_id=tid).exists(), "Unbound messaging work.")
                unused = plans.get() if plans.exists() else None
                require(not unused or not worker_events(unused).exists(), "Unbound worker receipts.")
                inv = dict(owner_id=owner_id(), messages=[], logs=[], outboxes=[], events=[], recipients=[],
                           plan_id=unused.pk if unused else 0, registration_id=0)
                if unused:
                    unused.delete()
            inv.update(setup_id=seal.pk, source_id=tid, scope_id=mark.pk, release=mark.payload["release"])
            inv.pop("deliveries", None)  # Only row IDs and cache keys are needed for recovery.
            mark.delete()
            OpsAuditLog.objects.create(action=PREFIX + "cleanup", actor_username=ACTOR, payload=inv, result="success")
            cleanup_fixture(code, capability)
    # New reads after commit also cover owner rows whose source FK would otherwise be SET_NULL.
    require(not connection.in_atomic_block, "Messaging residue requires committed reads.")
    residue = cleanup_fixture(code, capability)
    redis, locks, progress = cache_state(inv)
    residue.update(
        registration_batches=Registration.objects.filter(Q(tenant_id=tid) | Q(pk=inv["registration_id"])).count(),
        notice_recipients=EnrollmentNoticeRecipient.objects.filter(
            Q(registration_id=inv["registration_id"]) | Q(pk__in=inv["recipients"])).count(),
        notice_outboxes=ScheduledNotification.objects.filter(Q(tenant_id=tid) | Q(pk__in=inv["outboxes"])).count(),
        owner_notice_logs=NotificationLog.objects.filter(Q(source_tenant_id=tid) | Q(pk__in=inv["logs"])).count(),
        worker_evidence=OpsAuditLog.objects.filter(pk__in=[*inv["events"], inv["scope_id"], inv["plan_id"]]).count(),
        messaging_redis=redis.exists(*locks, *progress) if locks else 0,
    )
    require(not any(residue.values()), "Enrollment messaging cleanup did not reach zero.")
    return residue
