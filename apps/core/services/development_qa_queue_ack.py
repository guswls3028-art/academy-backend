"""Exact SQS acknowledgments for sealed development QA tenants."""
import os
import re
import time
from uuid import UUID

from django.db import OperationalError, close_old_connections

from apps.core.models import OpsAuditLog
from apps.core.services.development_qa_ownership import (
    MULTIPART_SETUP_SCHEMA, STANDARD_SETUP_SCHEMA, WRONG_NOTE_SETUP_SCHEMA,
)
from apps.domains.ai.models import AIJobModel


ACTION = "development.qa.sqs.ack"
WORKERS = {"question_segmentation": "CPU", "matchup_index_exam": "CPU", "wrong_note_pdf_generation": "TOOLS"}
QUEUES = {"CPU": "academy-v1-development-ai-queue", "TOOLS": "academy-v1-development-tools-queue"}
RELEASE = re.compile(r"sha-[0-9a-f]{40}-run-[0-9]+-[0-9]+|manual-sha256-[0-9a-f]{64}")


def record_ack(message):
    """Called by the consumer only after DeleteMessage returns success."""
    for attempt in range(3):
        try:
            return _record_ack(message)
        except OperationalError:
            if attempt == 2:
                raise
            close_old_connections()
            time.sleep(0.25 * (attempt + 1))


def _record_ack(message):
    worker = WORKERS.get(message.get("job_type"))
    if not worker:
        return
    tenant = str(message.get("tenant_id") or "")
    if (not re.fullmatch(r"[1-9][0-9]{0,17}", tenant)
            or os.environ.get("ACADEMY_RUNTIME_ENV") != "development"
            or os.environ.get("DJANGO_SETTINGS_MODULE") != "apps.api.config.settings.worker"
            or os.environ.get("DB_NAME") != "academy_api_development"
            or os.environ.get("DB_USER") != "academy_api_development_app"):
        return
    seals = list(OpsAuditLog.objects.filter(
        action__in=["development.qa.setup", "development.wrong-note.setup"], result="success",
        target_tenant_id=int(tenant), payload__tenant_id=int(tenant),
    ).values("pk", "action", "created_at", "target_tenant__code", "payload")[:2])
    if not seals:
        return
    if len(seals) != 1:
        raise PermissionError("Ambiguous development QA queue ownership")
    seal = seals[0]
    code = seal["target_tenant__code"]
    namespace = "wn" if seal["action"] == "development.wrong-note.setup" else "fe"
    schemas = (WRONG_NOTE_SETUP_SCHEMA,) if namespace == "wn" else (STANDARD_SETUP_SCHEMA, MULTIPART_SETUP_SCHEMA)
    release = os.environ.get("ACADEMY_DEVELOPMENT_RELEASE_ID", "")
    if (not re.fullmatch(rf"qa-ymath-realuse-{namespace}-[0-9]+-[0-9]+-[a-f0-9]{{12}}", code)
            or seal["payload"].get("tenant_code") != code
            or seal["payload"].get("schema") not in schemas
            or not re.fullmatch(r"[a-f0-9]{64}", str(seal["payload"].get("owner_sha256")))
            or os.environ.get("WORKER_TYPE") != worker or message.get("queue_name") != QUEUES[worker]
            or not RELEASE.fullmatch(release)):
        raise PermissionError("Development QA acknowledgment identity mismatch")
    job_id = message["job_id"]
    if not AIJobModel.objects.filter(tenant_id=tenant, job_id=job_id, job_type=message["job_type"],
                                     created_at__gte=seal["created_at"]).exists():
        return
    message_id = str(UUID(message["message_id"]))
    # A late ACK can arrive while Cleanup holds the tenant lock. The immutable
    # setup audit ID scopes this receipt without a foreign-key write to the tenant.
    OpsAuditLog.objects.get_or_create(action=ACTION, summary=job_id, actor_username=worker, result="success",
                                      payload={"tenant_id": int(tenant), "setup_id": seal["pk"],
                                               "release": release, "queue": QUEUES[worker],
                                               "job_type": message["job_type"], "message_id": message_id})


def require_acks(seal, jobs, *, wait_seconds=12):
    expected = {j.job_id: j.job_type for j in jobs if j.job_type in WORKERS}
    if not any(t in ("question_segmentation", "wrong_note_pdf_generation") for t in expected.values()):
        return
    release = os.environ.get("ACADEMY_DEVELOPMENT_RELEASE_ID", "")
    if not RELEASE.fullmatch(release):
        raise PermissionError("Development QA queue release identity missing")
    deadline = time.monotonic() + wait_seconds
    while True:
        rows = OpsAuditLog.objects.filter(action=ACTION, result="success", summary__in=expected,
                                          payload__setup_id=seal.pk, payload__tenant_id=seal.payload["tenant_id"],
                                          payload__release=release).values_list("summary", "actor_username", "payload")
        proven = set()
        for job_id, worker, payload in rows:
            kind = expected[job_id]
            if (worker == WORKERS[kind] and payload.get("job_type") == kind
                    and payload.get("queue") == QUEUES[worker]
                    and re.fullmatch(r"[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}", str(payload.get("message_id")))):
                proven.add(job_id)
        if proven == set(expected):
            return
        if time.monotonic() >= deadline:
            raise PermissionError(f"Missing confirmed development SQS acknowledgments for {len(expected) - len(proven)} job(s)")
        time.sleep(0.3)
