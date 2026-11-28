"""Run only on the sole active candidate in the persistent development release gate."""
import hashlib
import io
import json
import os
import re
import secrets
import stat
import time
from pathlib import Path
from tempfile import gettempdir

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

from apps.core.management.commands.setup_ymath_realuse_scenario import (
    Command as Scenario, DEVELOPMENT_SETTINGS_MODULE, PASSWORD_ENV, assert_isolated_runtime,
)
from apps.core.models import OpsAuditLog, Tenant
from apps.core.services.development_qa_ai_cleanup import OwnedQaAI, TERMINAL
from apps.core.services.development_qa_ownership import WRONG_NOTE_SETUP_SCHEMA
from apps.domains.ai.models import AIJobModel


def check_boundary(release, production_database):
    if (not re.fullmatch(r"sha-[0-9a-f]{40}-run-[0-9]+-[0-9]+|manual-sha256-[0-9a-f]{64}", release)
            or not re.fullmatch(r"[a-z][a-z0-9_]{2,62}", production_database)
            or os.environ.get("DJANGO_SETTINGS_MODULE") != DEVELOPMENT_SETTINGS_MODULE
            or os.environ.get("ACADEMY_RUNTIME_ENV") != "development"
            or os.environ.get("ACADEMY_DEVELOPMENT_RELEASE_ID") != release):
        raise CommandError("Wrong-note QA requires the exact isolated development release")
    assert_isolated_runtime()
    for setting, name in [
        ("TOOLS_SQS_QUEUE_NAME", "academy-v1-development-tools-queue"),
        *((f"AI_SQS_QUEUE_NAME_{tier}", "academy-v1-development-ai-queue") for tier in ("LITE", "BASIC", "PREMIUM")),
    ]:
        if os.environ.get(setting) != name:
            raise CommandError("Wrong-note QA queue is outside development")
    if os.environ.get("REDIS_HOST") != "127.0.0.1" or os.environ.get("REDIS_PORT", "6379") != "6379":
        raise CommandError("Wrong-note QA requires the sole active host's loopback cache")
    with connection.cursor() as cursor:
        cursor.execute("SELECT has_database_privilege(current_user,%s,'CONNECT')", [production_database])
        if cursor.fetchone() != (False,):
            raise CommandError("Expected production database denial")


def setup(code, capability, password):
    with transaction.atomic():
        Scenario._lock_tenant_code(code)
        if Tenant.objects.filter(code__iexact=code).exists() or OpsAuditLog.objects.filter(
            action="development.wrong-note.setup", payload__tenant_code=code,
        ).exists():
            raise CommandError("Refusing to adopt a pre-existing QA tenant or ownership seal")
        old = os.environ.get(PASSWORD_ENV)
        try:
            os.environ[PASSWORD_ENV] = password
            call_command("setup_ymath_realuse_scenario", tenant_code=code, student_count=2, session_count=2, stdout=io.StringIO())
        finally:
            if old is None:
                os.environ.pop(PASSWORD_ENV, None)
            else:
                os.environ[PASSWORD_ENV] = old
        tenant = Tenant.objects.get(code=code)
        OpsAuditLog.objects.create(action="development.wrong-note.setup", actor_username="wrong-note-development-runner",
                                   result="success", target_tenant=tenant, payload={
            "schema": WRONG_NOTE_SETUP_SCHEMA, "tenant_code": code, "tenant_id": tenant.pk,
            "owner_sha256": hashlib.sha256(f"{code}:{tenant.pk}:{capability}".encode()).hexdigest(),
        })
    return tenant


def cleanup(code, capability):
    seal = OpsAuditLog.objects.get(action="development.wrong-note.setup", result="success", payload__tenant_code=code)
    tenant_id = seal.payload["tenant_id"]
    ai = OwnedQaAI(code, tenant_id, capability, wrong_note_smoke=True)
    if ai.present:
        deadline = time.monotonic() + 180
        # Approval can also schedule downstream indexing; let its consumers finish before touching storage.
        while AIJobModel.objects.filter(tenant_id=str(tenant_id)).exclude(
            status__in=TERMINAL, completed_at__isnull=False, locked_by__isnull=True,
            locked_at__isnull=True, lease_expires_at__isnull=True,
        ).exists():
            if time.monotonic() >= deadline:
                raise CommandError("Owned QA jobs still running. Preserve the scope for cleanup-only recovery.")
            time.sleep(2)
        with transaction.atomic():
            Scenario._lock_tenant_code(code)
            tenant = Tenant.objects.select_for_update().get(pk=tenant_id, code=code)
            user_ids = list(tenant.users.values_list("pk", flat=True))
            plan = ai.prepare()
            if Scenario._cleanup_qa_r2_objects(tenant_id=tenant_id)["remaining"] != 0:
                raise CommandError("QA storage cleanup incomplete")
            ai.delete(plan)
            output = io.StringIO()
            call_command("setup_ymath_realuse_scenario", tenant_code=code, destroy=True, stdout=output)
            destroyed = json.loads(output.getvalue().strip().splitlines()[-1])
            if (destroyed.get("tenant_id") != tenant_id or any(destroyed["residue"].values())
                    or get_user_model().objects.filter(pk__in=user_ids).exists()):
                raise CommandError("QA account cleanup incomplete")
    residue = {
        **Scenario._remaining_for_code(code),
        **Scenario._non_database_residue(tenant_id=tenant_id, tenant_code=code),
        **OwnedQaAI(code, tenant_id, capability, wrong_note_smoke=True).inspect(),
    }
    if any(residue.values()):
        raise CommandError("Wrong-note QA cleanup readback is not zero")
    return residue


class Command(BaseCommand):
    help = "Verify the actual AI/Tools wrong-note flow on an immutable, exclusive development release."

    def add_arguments(self, parser):
        parser.add_argument("--release", required=True)
        parser.add_argument("--production-database", required=True)
        parser.add_argument("--cleanup-only", action="store_true")
        modes = parser.add_mutually_exclusive_group()
        modes.add_argument("--enrollment-notices", action="store_true")
        modes.add_argument("--enrollment-tick", type=int, metavar="SEALED_PLAN_ID")
        modes.add_argument("--clinic-notices", action="store_true")
        parser.add_argument("--enrollment-recipient", choices=("student", "parent"))

    def handle(self, *args, **options):
        # Reserve a host-local recovery capability, never emitted through SSM or copied to a runner.
        release = options["release"]
        check_boundary(release, options["production_database"])
        import fcntl
        from apps.core.services.wrong_note_development_scenario import UncertainWrite, run

        notices = options["enrollment_notices"]
        role = options["enrollment_recipient"] or "student"
        if options["enrollment_recipient"] and (not notices or options["cleanup_only"]):
            raise CommandError("Recipient selection requires a new enrollment scenario.")
        cleanup_scope = cleanup
        clinic = options["clinic_notices"]
        if clinic:
            from apps.core.services import clinic_notice_development_scenario as clinic_notice
            from apps.core.services.enrollment_notice_development_scenario import check_mock

            check_mock()
            cleanup_scope = clinic_notice.cleanup
        elif notices or options["enrollment_tick"] is not None:
            from apps.core.services import enrollment_notice_development_scenario as notice
            notice.check_mock()
            if options["enrollment_tick"] is not None:
                if options["cleanup_only"]:
                    raise CommandError("Cannot combine a scheduled tick with cleanup.")
                notice.run_scheduler(options["enrollment_tick"], release, options["production_database"])
                return
            cleanup_scope = notice.cleanup
        scenario = "clinic-notice" if clinic else "enrollment-notice" if notices else "wrong-note"
        status_prefix = scenario.upper().replace("-", "_")

        directory = Path(gettempdir()) / f"academy-{scenario}-{hashlib.sha256(release.encode()).hexdigest()[:24]}"
        directory.mkdir(mode=0o700, exist_ok=True)
        dstat = directory.lstat()
        if not stat.S_ISDIR(dstat.st_mode) or dstat.st_uid != os.geteuid() or stat.S_IMODE(dstat.st_mode) != 0o700:
            raise CommandError("Unsafe QA recovery directory")
        fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            state = directory / "owned-scope.json"
            if options["cleanup_only"]:
                saved = json.loads(state.read_text())
                residue = cleanup_scope(saved["code"], saved["capability"])
                state.unlink()
                receipt = {"status": f"{status_prefix}_CLEANUP_PASS", "remaining": residue}
                if notices:
                    receipt["selected_role"] = saved.get("selected_role", "student")
                self.stdout.write(json.dumps(receipt))
                return
            if state.exists():
                raise CommandError("An owned scope awaits cleanup on this release. Recover it before another run.")
            run_number = re.search(r"-run-(\d+)-(\d+)$", release)
            run_label = "-".join(run_number.groups()) if run_number else "0-0"
            code = f"qa-ymath-realuse-wn-{run_label}-{secrets.token_hex(6)}"
            capability = secrets.token_hex(32)
            password = secrets.token_urlsafe(32)
            with state.open("x", encoding="utf-8") as stream:
                os.fchmod(stream.fileno(), 0o600)
                owned_scope = {"code": code, "capability": capability}
                if notices:
                    owned_scope["selected_role"] = role
                json.dump(owned_scope, stream)
                stream.flush()
                os.fsync(stream.fileno())
            try:
                if clinic:
                    with transaction.atomic():
                        tenant = setup(code, capability, password)
                        clinic_notice.mark_scope(tenant, release)
                elif notices:
                    with transaction.atomic():
                        tenant = setup(code, capability, password)
                        notice.mark_scope(tenant, release, role)
                        notice.prepare_families(tenant, password)
                else:
                    tenant = setup(code, capability, password)
            except Exception:
                # Setup writes its tenant and seal in a single transaction. If the commit is ambiguous keep the capability.
                if not OpsAuditLog.objects.filter(action="development.wrong-note.setup", payload__tenant_code=code).exists():
                    state.unlink()
                raise
            self.stdout.write(f"{status_prefix}_QA_STARTED tenant={code} id={tenant.pk}")
            try:
                if clinic:
                    result = clinic_notice.run(tenant, password, release=release,
                                               production_database=options["production_database"])
                elif notices:
                    result = notice.run(tenant, password, selected_role=role, release=release,
                                        production_database=options["production_database"])
                else:
                    result = run(tenant, password)
            except UncertainWrite:
                # Unacknowledged writes can commit after a timeout. Retain exact recovery ownership.
                raise
            except Exception:
                cleanup_scope(code, capability)
                state.unlink()
                raise
            residue = cleanup_scope(code, capability)
            state.unlink()
            self.stdout.write(json.dumps({"status": f"{status_prefix}_REAL_USE_PASS", **result, "remaining": residue}))
        finally:
            os.close(fd)
