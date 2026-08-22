"""Atomically recover the exact 2026-08-22 first-enrollment notice incident.

The command is deliberately incident-scoped and dry-run by default. It never
prints credentials or recipient data, and it cannot target a tenant or student
outside the reviewed allowlist.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q

from apps.core.models import PendingPasswordReset, Tenant
from apps.core.services.password import force_reset_password, generate_temp_password
from apps.domains.enrollment.models import Enrollment
from apps.domains.messaging.models import (
    AutoSendConfig,
    MessageTemplate,
    NotificationLog,
    ScheduledNotification,
)
from apps.domains.messaging.policy import (
    get_owner_tenant_id,
    is_messaging_ops_held,
    is_messaging_runtime_held,
)
from apps.domains.messaging.services import get_solapi_client
from apps.domains.messaging.sqs_queue import MessagingSQSQueue
from apps.domains.parents.models import Parent
from apps.domains.students.models import Student
from apps.domains.students.services.account_notice import (
    dispatch_pending_account_notice,
    stage_pending_account_notice,
)
from apps.domains.students.services.account_notifications import (
    send_student_account_credentials_notice,
)
from libs.queue import get_queue_client


EXPECTED_TENANT_ID = 11
EXPECTED_TOKEN_VERSION = 2
STUDENT_ONLY_IDS = frozenset({3656})
PAIR_IDS = frozenset({4102, 4103, 4104, 4105})
EXPECTED_STUDENT_IDS = tuple(sorted(STUDENT_ONLY_IDS | PAIR_IDS))
EXPECTED_ROTATED_COUNT = 9
EXPECTED_OUTBOX_COUNT = 9

FAILURE_REASON = "business_tenant_messaging_disabled"
ORIGINAL_ORIGIN_TYPE = "excel_import"
RECOVERY_ORIGIN_TYPE = "recovery"
RECOVERY_ORIGIN_PREFIX = "credential-incident"
STUDENT_TRIGGER = "registration_approved_student"
PARENT_TRIGGER = "registration_approved_parent"
ACCOUNT_TRIGGERS = (STUDENT_TRIGGER, PARENT_TRIGGER)


@dataclass(frozen=True)
class RecoveryCandidate:
    student: Student
    mode: str
    historical_outbox_ids: tuple[int, ...]
    historical_log_ids: tuple[int, ...]


def _parse_student_ids(raw: str) -> list[int]:
    values = [value.strip() for value in str(raw or "").split(",") if value.strip()]
    try:
        parsed = [int(value) for value in values]
    except ValueError as exc:
        raise CommandError("student_ids_must_be_comma_separated_integers") from exc
    if not parsed or any(value <= 0 for value in parsed):
        raise CommandError("student_ids_required")
    if len(parsed) != len(set(parsed)):
        raise CommandError("duplicate_student_ids_not_allowed")
    parsed = sorted(parsed)
    if tuple(parsed) != EXPECTED_STUDENT_IDS:
        raise CommandError("student_ids_must_match_reviewed_incident_allowlist")
    return parsed


def _payload_target(notification: ScheduledNotification) -> str:
    payload = notification.payload if isinstance(notification.payload, dict) else {}
    return str(payload.get("target_id") or "").strip()


def _normalize_phone(value: object) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def _log_scope(tenant: Tenant) -> Q:
    return Q(source_tenant=tenant) | Q(tenant=tenant, source_tenant__isnull=True)


def _provider_sent_logs(
    logs: list[NotificationLog],
    *,
    trigger: str,
    target_id: str,
) -> list[NotificationLog]:
    return [
        log
        for log in logs
        if (
            log.notification_type == trigger
            and log.target_id == target_id
            and log.status == "sent"
            and log.success
            and bool(log.provider_message_id)
        )
    ]


def _origin_id(student_id: int) -> str:
    return f"{RECOVERY_ORIGIN_PREFIX}:{student_id}"


def _assert_queue_empty() -> None:
    try:
        client = get_queue_client()
        for queue_name in (
            getattr(settings, "MESSAGING_SQS_QUEUE_NAME", MessagingSQSQueue.QUEUE_NAME),
            MessagingSQSQueue.DLQ_NAME,
        ):
            counts = client.get_queue_counts(queue_name)
            if any(int(counts.get(key, 0)) != 0 for key in ("visible", "not_visible", "delayed")):
                raise CommandError(f"messaging_queue_not_empty:{queue_name}")
    except CommandError:
        raise
    except Exception as exc:
        raise CommandError("messaging_queue_health_unavailable") from exc


def _assert_runtime_preflight(*, tenant: Tenant, lock: bool) -> None:
    if not tenant.messaging_is_active:
        raise CommandError("business_tenant_messaging_inactive")
    if is_messaging_ops_held(tenant.id):
        raise CommandError("business_tenant_messaging_ops_held")

    owner_id = int(get_owner_tenant_id())
    owner_query = Tenant.objects.filter(pk=owner_id, is_active=True)
    if lock:
        owner_query = owner_query.select_for_update()
    if not owner_query.exists() or is_messaging_runtime_held(owner_id):
        raise CommandError("owner_messaging_runtime_unavailable")

    config_query = AutoSendConfig.objects.filter(
        tenant_id=owner_id,
        trigger__in=ACCOUNT_TRIGGERS,
    ).order_by("trigger")
    if lock:
        config_query = config_query.select_for_update()
    configs = list(config_query)
    if {config.trigger for config in configs} != set(ACCOUNT_TRIGGERS):
        raise CommandError("exact_owner_account_templates_required")

    template_ids = [config.template_id for config in configs if config.template_id]
    template_query = MessageTemplate.objects.filter(id__in=template_ids).order_by("id")
    if lock:
        template_query = template_query.select_for_update()
    templates = {template.id: template for template in template_query}
    for config in configs:
        template = templates.get(config.template_id)
        if (
            config.message_mode != "alimtalk"
            or template is None
            or template.tenant_id != owner_id
            or template.solapi_status != "APPROVED"
            or not str(template.solapi_template_id or "").strip()
        ):
            raise CommandError(f"owner_template_drift:{config.trigger}")

    try:
        threshold = Decimal(
            str(getattr(settings, "MESSAGING_PROVIDER_LOW_BALANCE_ALERT_THRESHOLD", 10_000))
        )
        client = get_solapi_client()
        if client is None:
            raise CommandError("provider_balance_unavailable")
        response = client.get_balance()
        balance = Decimal(str(getattr(response, "balance", None)))
    except CommandError:
        raise
    except Exception as exc:
        raise CommandError("provider_balance_unavailable") from exc
    if balance < threshold:
        raise CommandError("provider_balance_below_recovery_threshold")

    _assert_queue_empty()


def _load_candidates(
    *,
    tenant: Tenant,
    student_ids: list[int],
    lock: bool,
) -> list[RecoveryCandidate]:
    base_students = Student.objects.filter(
        tenant=tenant,
        id__in=student_ids,
        deleted_at__isnull=True,
    )
    if lock:
        list(base_students.select_for_update().order_by("id"))

    students = list(
        base_students.select_related("user", "parent__user").order_by("id")
    )
    found_ids = [student.id for student in students]
    if found_ids != student_ids:
        missing = sorted(set(student_ids) - set(found_ids))
        raise CommandError(f"active_students_not_found:{','.join(map(str, missing))}")

    parent_ids = [student.parent_id for student in students if student.parent_id]
    parent_query = Parent.objects.filter(id__in=parent_ids).order_by("id")
    if lock:
        list(parent_query.select_for_update())
    parents = list(parent_query.select_related("user"))
    parent_by_id = {parent.id: parent for parent in parents}

    user_ids = [student.user_id for student in students]
    user_ids.extend(parent.user_id for parent in parents if parent.user_id)
    user_query = get_user_model().objects.filter(id__in=user_ids).order_by("id")
    if lock:
        list(user_query.select_for_update())
    users = {user.id: user for user in user_query}

    enrollment_query = Enrollment.objects.filter(
        tenant=tenant,
        student_id__in=student_ids,
        status="ACTIVE",
    ).order_by("id")
    if lock:
        list(enrollment_query.select_for_update())
    active_enrollment_counts = Counter(enrollment_query.values_list("student_id", flat=True))

    pending_query = PendingPasswordReset.objects.filter(user_id__in=user_ids).order_by("id")
    if lock:
        list(pending_query.select_for_update())
    if pending_query.exists():
        raise CommandError("pending_password_reset_exists")

    expected_targets = {
        target
        for student_id in student_ids
        for target in (f"student:{student_id}", f"parent:{student_id}")
    }
    outbox_query = ScheduledNotification.objects.filter(
        tenant=tenant,
        trigger__in=ACCOUNT_TRIGGERS,
    ).order_by("created_at", "id")
    if lock:
        list(outbox_query.select_for_update())
    outboxes = [row for row in outbox_query if _payload_target(row) in expected_targets]

    log_query = NotificationLog.objects.filter(
        _log_scope(tenant),
        notification_type__in=ACCOUNT_TRIGGERS,
        target_id__in=expected_targets,
    ).order_by("sent_at", "id")
    if lock:
        list(log_query.select_for_update())
    logs = list(log_query)

    parent_user_ids: list[int] = []
    candidates: list[RecoveryCandidate] = []
    for student in students:
        mode = "student_only" if student.id in STUDENT_ONLY_IDS else "pair"
        student_user = users.get(student.user_id)
        parent = parent_by_id.get(student.parent_id)
        parent_user = users.get(parent.user_id) if parent and parent.user_id else None
        if student_user is None or student_user.tenant_id != tenant.id:
            raise CommandError(f"student_user_tenant_mismatch:student_id={student.id}")
        if parent is None or parent_user is None:
            raise CommandError(f"parent_account_missing:student_id={student.id}")
        if parent.tenant_id != tenant.id or parent_user.tenant_id != tenant.id:
            raise CommandError(f"parent_user_tenant_mismatch:student_id={student.id}")
        if not student_user.is_active or not parent_user.is_active:
            raise CommandError(f"account_inactive:student_id={student.id}")
        if student_user.last_login is not None or parent_user.last_login is not None:
            raise CommandError(f"account_already_used:student_id={student.id}")
        if (
            int(student_user.token_version) != EXPECTED_TOKEN_VERSION
            or int(parent_user.token_version) != EXPECTED_TOKEN_VERSION
        ):
            raise CommandError(f"token_version_drift:student_id={student.id}")
        if active_enrollment_counts[student.id] < 1:
            raise CommandError(f"active_enrollment_required:student_id={student.id}")
        if (
            student.pending_account_notice_student_password_ciphertext
            or student.pending_account_notice_parent_password_ciphertext
            or student.pending_account_notice_since is not None
        ):
            raise CommandError(f"pending_account_notice_exists:student_id={student.id}")
        if Student.objects.filter(user_id=student_user.id, deleted_at__isnull=True).count() != 1:
            raise CommandError(f"student_user_profile_drift:student_id={student.id}")
        if Parent.objects.filter(user_id=parent_user.id).count() != 1:
            raise CommandError(f"parent_user_profile_drift:student_id={student.id}")

        student_phone = _normalize_phone(student.phone)
        parent_phone = _normalize_phone(student.parent_phone)
        canonical_parent_phone = _normalize_phone(parent.phone)
        if (
            len(student_phone) < 10
            or len(parent_phone) < 10
            or student_phone == parent_phone
            or parent_phone != canonical_parent_phone
        ):
            raise CommandError(f"recipient_scope_invalid:student_id={student.id}")

        active_siblings = (
            Student.objects.filter(
                tenant=tenant,
                parent_id=parent.id,
                deleted_at__isnull=True,
                user__is_active=True,
            )
            .exclude(id=student.id)
            .count()
        )
        if mode == "student_only" and active_siblings < 1:
            raise CommandError(f"reviewed_shared_parent_missing:student_id={student.id}")
        if mode == "pair" and active_siblings != 0:
            raise CommandError(f"parent_shared_with_active_sibling:student_id={student.id}")
        parent_user_ids.append(parent_user.id)

        targets = {
            STUDENT_TRIGGER: f"student:{student.id}",
            PARENT_TRIGGER: f"parent:{student.id}",
        }
        matched = {
            trigger: [
                row
                for row in outboxes
                if row.trigger == trigger and _payload_target(row) == target
            ]
            for trigger, target in targets.items()
        }
        if any(
            row.origin_type == RECOVERY_ORIGIN_TYPE
            for rows in matched.values()
            for row in rows
        ):
            raise CommandError(f"recovery_outbox_already_exists:student_id={student.id}")

        student_sent = _provider_sent_logs(
            logs,
            trigger=STUDENT_TRIGGER,
            target_id=targets[STUDENT_TRIGGER],
        )
        parent_sent = _provider_sent_logs(
            logs,
            trigger=PARENT_TRIGGER,
            target_id=targets[PARENT_TRIGGER],
        )
        if mode == "student_only":
            if not matched[STUDENT_TRIGGER]:
                raise CommandError(f"student_notice_history_required:student_id={student.id}")
            if student_sent:
                raise CommandError(
                    f"later_success_already_exists:student_id={student.id}:trigger={STUDENT_TRIGGER}"
                )
            student_failure_logs = [
                log
                for log in logs
                if log.notification_type == STUDENT_TRIGGER
                and log.target_id == targets[STUDENT_TRIGGER]
                and (not log.success or log.status in {"ambiguous", "failed"})
            ]
            if not student_failure_logs:
                raise CommandError(
                    f"reviewed_student_failure_required:student_id={student.id}"
                )
            if (
                len(parent_sent) != 1
                or parent_sent[0].message_mode != "alimtalk"
                or not parent_sent[0].business_idempotency_key
            ):
                raise CommandError(f"reviewed_parent_success_required:student_id={student.id}")
        else:
            for trigger, rows in matched.items():
                if len(rows) != 1:
                    raise CommandError(
                        f"exact_failed_outbox_pair_required:student_id={student.id}:"
                        f"trigger={trigger}:count={len(rows)}"
                    )
                row = rows[0]
                if (
                    row.status != ScheduledNotification.Status.FAILED
                    or row.error_message != FAILURE_REASON
                    or row.origin_type != ORIGINAL_ORIGIN_TYPE
                ):
                    raise CommandError(
                        f"outbox_not_eligible:student_id={student.id}:trigger={trigger}"
                    )
            if student_sent or parent_sent:
                trigger = STUDENT_TRIGGER if student_sent else PARENT_TRIGGER
                raise CommandError(
                    f"later_success_already_exists:student_id={student.id}:trigger={trigger}"
                )

        candidate_logs = [
            log.id
            for log in logs
            if log.target_id in targets.values()
        ]
        candidates.append(
            RecoveryCandidate(
                student=student,
                mode=mode,
                historical_outbox_ids=tuple(
                    row.id for rows in matched.values() for row in rows
                ),
                historical_log_ids=tuple(candidate_logs),
            )
        )

    if len(parent_user_ids) != len(set(parent_user_ids)):
        raise CommandError("shared_parent_account_in_student_selection")
    return candidates


def _historical_state(
    *,
    outbox_ids: set[int],
    log_ids: set[int],
) -> tuple[list[tuple], list[tuple]]:
    outboxes = list(
        ScheduledNotification.objects.filter(id__in=outbox_ids)
        .order_by("id")
        .values_list(
            "id",
            "status",
            "error_message",
            "business_idempotency_key",
            "origin_type",
            "origin_id",
            "payload",
        )
    )
    logs = list(
        NotificationLog.objects.filter(id__in=log_ids)
        .order_by("id")
        .values_list(
            "id",
            "status",
            "success",
            "provider_message_id",
            "failure_reason",
            "amount_deducted",
            "business_idempotency_key",
            "message_mode",
            "notification_type",
            "target_id",
        )
    )
    return outboxes, logs


def _apply_candidates(*, tenant: Tenant, candidates: list[RecoveryCandidate]) -> dict:
    outbox_ids = {
        row_id for candidate in candidates for row_id in candidate.historical_outbox_ids
    }
    log_ids = {row_id for candidate in candidates for row_id in candidate.historical_log_ids}
    historical_before = _historical_state(outbox_ids=outbox_ids, log_ids=log_ids)

    initial_user_state: dict[int, tuple[int, str, bool]] = {}
    rotated_user_ids: set[int] = set()
    origin_ids: list[str] = []
    for candidate in candidates:
        student = candidate.student
        student.user.refresh_from_db()
        student.parent.user.refresh_from_db()
        for user in (student.user, student.parent.user):
            initial_user_state[user.id] = (
                int(user.token_version),
                str(user.password),
                bool(user.must_change_password),
            )

        student_password = generate_temp_password()
        force_reset_password(student.user, student_password)
        rotated_user_ids.add(student.user_id)
        recovery_origin_id = _origin_id(student.id)
        origin_ids.append(recovery_origin_id)

        if candidate.mode == "student_only":
            if not send_student_account_credentials_notice(
                student=student,
                password=student_password,
                origin_type=RECOVERY_ORIGIN_TYPE,
                origin_id=recovery_origin_id,
            ):
                raise CommandError(
                    f"student_replacement_outbox_not_created:student_id={student.id}"
                )
            continue

        parent_password = generate_temp_password()
        force_reset_password(student.parent.user, parent_password)
        rotated_user_ids.add(student.parent.user_id)
        stage_pending_account_notice(
            student=student,
            student_password=student_password,
            parent_password=parent_password,
            origin_type=RECOVERY_ORIGIN_TYPE,
            origin_id=recovery_origin_id,
        )
        result = dispatch_pending_account_notice(student_id=student.id)
        if result.get("status") != "enqueued" or result.get("enqueued") != 2:
            raise CommandError(
                f"replacement_outbox_pair_not_created:student_id={student.id}"
            )

    if len(rotated_user_ids) != EXPECTED_ROTATED_COUNT:
        raise CommandError("credential_rotation_count_mismatch")

    users = {
        user.id: user
        for user in get_user_model().objects.filter(id__in=rotated_user_ids)
    }
    for user_id in rotated_user_ids:
        user = users[user_id]
        before_version = initial_user_state[user_id][0]
        if user.token_version != before_version + 1 or not user.must_change_password:
            raise CommandError("credential_rotation_state_mismatch")

    student_only_parent_id = next(
        candidate.student.parent.user_id
        for candidate in candidates
        if candidate.mode == "student_only"
    )
    student_only_parent = get_user_model().objects.get(id=student_only_parent_id)
    parent_before = initial_user_state[student_only_parent_id]
    if (
        student_only_parent.token_version != parent_before[0]
        or student_only_parent.password != parent_before[1]
        or student_only_parent.must_change_password != parent_before[2]
    ):
        raise CommandError("student_only_parent_mutated")

    new_outboxes = list(
        ScheduledNotification.objects.filter(
            tenant=tenant,
            origin_type=RECOVERY_ORIGIN_TYPE,
            origin_id__in=origin_ids,
        ).order_by("id")
    )
    if len(new_outboxes) != EXPECTED_OUTBOX_COUNT:
        raise CommandError("replacement_outbox_count_mismatch")

    expected_pairs = Counter()
    for candidate in candidates:
        expected_pairs[(STUDENT_TRIGGER, f"student:{candidate.student.id}")] += 1
        if candidate.mode == "pair":
            expected_pairs[(PARENT_TRIGGER, f"parent:{candidate.student.id}")] += 1
    actual_pairs = Counter()
    for outbox in new_outboxes:
        payload = outbox.payload if isinstance(outbox.payload, dict) else {}
        if (
            payload.get("message_mode") != "alimtalk"
            or int(payload.get("source_tenant_id") or 0) != tenant.id
            or outbox.status != ScheduledNotification.Status.PENDING
        ):
            raise CommandError("replacement_outbox_contract_mismatch")
        actual_pairs[(outbox.trigger, str(payload.get("target_id") or ""))] += 1
    if actual_pairs != expected_pairs:
        raise CommandError("replacement_outbox_target_count_mismatch")

    for candidate in candidates:
        candidate.student.refresh_from_db()
        if (
            candidate.student.pending_account_notice_student_password_ciphertext
            or candidate.student.pending_account_notice_parent_password_ciphertext
            or candidate.student.pending_account_notice_since is not None
        ):
            raise CommandError("pending_account_notice_not_cleared")

    historical_after = _historical_state(outbox_ids=outbox_ids, log_ids=log_ids)
    if historical_after != historical_before:
        raise CommandError("historical_delivery_state_mutated")

    _assert_queue_empty()
    return {
        "credentials_rotated": len(rotated_user_ids),
        "outboxes_created": len(new_outboxes),
    }


class Command(BaseCommand):
    help = (
        "Dry-run by default. Recover the exact reviewed first-enrollment incident "
        "with one student-only rotation and four non-shared student/parent pairs."
    )

    def add_arguments(self, parser):
        parser.add_argument("--tenant-id", type=int, required=True)
        parser.add_argument(
            "--student-ids",
            required=True,
            help="Exact reviewed comma-separated student ID allowlist",
        )
        parser.add_argument("--apply", action="store_true")
        parser.add_argument(
            "--confirm-tenant",
            default="",
            help="For --apply, repeat the exact Tenant.code",
        )

    def handle(self, *args, **options):
        tenant_id = int(options["tenant_id"])
        if tenant_id != EXPECTED_TENANT_ID:
            raise CommandError("tenant_id_not_in_reviewed_incident")
        student_ids = _parse_student_ids(options["student_ids"])
        apply_changes = bool(options["apply"])

        result: dict | None = None
        with transaction.atomic():
            tenant_query = Tenant.objects.filter(id=tenant_id, is_active=True)
            if apply_changes:
                tenant_query = tenant_query.select_for_update()
            tenant = tenant_query.first()
            if tenant is None:
                raise CommandError(f"active_tenant_not_found:{tenant_id}")
            if (
                apply_changes
                and str(options.get("confirm_tenant") or "").strip() != tenant.code
            ):
                raise CommandError(
                    "confirmation_required:--confirm-tenant must equal Tenant.code"
                )

            candidates = _load_candidates(
                tenant=tenant,
                student_ids=student_ids,
                lock=apply_changes,
            )
            _assert_runtime_preflight(tenant=tenant, lock=apply_changes)
            mode_counts = Counter(candidate.mode for candidate in candidates)
            mode = "apply" if apply_changes else "dry-run"
            self.stdout.write(
                f"first_enrollment_notice_recovery tenant_id={tenant.id} "
                f"mode={mode} candidates={len(candidates)} "
                f"student_only={mode_counts['student_only']} pairs={mode_counts['pair']} "
                f"expected_credentials_rotated={EXPECTED_ROTATED_COUNT} "
                f"expected_outboxes={EXPECTED_OUTBOX_COUNT} "
                "account_last_login=none recipients=valid_distinct secrets=redacted"
            )
            if apply_changes:
                result = _apply_candidates(tenant=tenant, candidates=candidates)

        if result is not None:
            self.stdout.write(
                self.style.SUCCESS(
                    f"first_enrollment_notice_recovery_complete tenant_id={tenant_id} "
                    f"students={len(EXPECTED_STUDENT_IDS)} "
                    f"credentials_rotated={result['credentials_rotated']} "
                    f"outboxes_created={result['outboxes_created']} secrets=redacted"
                )
            )
