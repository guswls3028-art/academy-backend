"""Atomically recover the exact 2026-08-22 first-enrollment notice incident.

The command is deliberately incident-scoped and dry-run by default. It never
prints credentials or recipient data, and it cannot target a tenant or student
outside the reviewed allowlist.
"""

from __future__ import annotations

import re
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError, connection, transaction

from apps.core.models import PendingPasswordReset, Tenant
from apps.core.models.user import user_display_username
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
    resolve_kakao_channel,
)
from apps.domains.messaging.services import _get_solapi_credentials, get_solapi_client
from apps.domains.messaging.solapi_sender_client import get_active_sender_numbers
from apps.domains.messaging.solapi_template_client import list_kakao_templates
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
REQUIRED_PLACEHOLDERS = {
    STUDENT_TRIGGER: frozenset(
        {"학생이름", "학생아이디", "학생비밀번호", "사이트링크", "비밀번호안내"}
    ),
    PARENT_TRIGGER: frozenset(
        {
            "학생이름",
            "학생아이디",
            "학생비밀번호",
            "학부모아이디",
            "학부모비밀번호",
            "사이트링크",
            "비밀번호안내",
        }
    ),
}
PLACEHOLDER_PATTERN = re.compile(r"#\{([^}]+)\}")


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


def _has_provider_acceptance_evidence(log: NotificationLog) -> bool:
    return (
        bool(log.provider_message_id)
        or Decimal(log.amount_deducted) > Decimal("0")
        or bool(log.success)
        or log.status in {"processing", "sending", "sent", "ambiguous"}
    )


def _is_exact_balance_rejection(
    log: NotificationLog,
    *,
    owner_tenant_id: int,
    tenant_id: int,
    target_id: str,
    business_idempotency_key: str,
) -> bool:
    reason = str(log.failure_reason or "").strip()
    exact_reason = reason == "NotEnoughBalance" or reason.startswith(
        "('NotEnoughBalance',"
    )
    return (
        log.tenant_id == owner_tenant_id
        and log.source_tenant_id == tenant_id
        and log.notification_type == STUDENT_TRIGGER
        and log.target_type == "account"
        and log.target_id == target_id
        and log.message_mode == "alimtalk"
        and log.status in {"ambiguous", "failed"}
        and not log.success
        and not log.provider_message_id
        and Decimal(log.amount_deducted) == Decimal("0")
        and log.business_idempotency_key == business_idempotency_key
        and log.origin_type == "system_account"
        and log.origin_id == target_id
        and exact_reason
    )


def _is_exact_parent_success(
    log: NotificationLog,
    *,
    owner_tenant_id: int,
    tenant_id: int,
    target_id: str,
    business_idempotency_key: str,
) -> bool:
    return (
        log.tenant_id == owner_tenant_id
        and log.source_tenant_id == tenant_id
        and log.notification_type == PARENT_TRIGGER
        and log.target_type == "account"
        and log.target_id == target_id
        and log.message_mode == "alimtalk"
        and log.status == "sent"
        and log.success
        and bool(log.provider_message_id)
        and log.business_idempotency_key == business_idempotency_key
        and log.origin_type == "system_account"
        and log.origin_id == target_id
    )


def _is_allowed_parent_ambiguous_history(
    log: NotificationLog,
    *,
    owner_tenant_id: int,
    tenant_id: int,
    target_id: str,
) -> bool:
    return (
        log.tenant_id == owner_tenant_id
        and log.source_tenant_id == tenant_id
        and log.notification_type == PARENT_TRIGGER
        and log.target_type == "account"
        and log.target_id == target_id
        and log.message_mode == "alimtalk"
        and log.status == "ambiguous"
        and not log.success
        and not log.provider_message_id
        and Decimal(log.amount_deducted) == Decimal("0")
    )


def _is_exact_system_account_outbox(
    notification: ScheduledNotification,
    *,
    tenant_id: int,
    trigger: str,
    target_id: str,
) -> bool:
    payload = notification.payload if isinstance(notification.payload, dict) else {}
    return (
        notification.tenant_id == tenant_id
        and notification.status == ScheduledNotification.Status.SENT
        and not notification.error_message
        and bool(notification.dispatch_key)
        and bool(notification.business_idempotency_key)
        and notification.origin_type == "system_account"
        and notification.origin_id == target_id
        and payload.get("event_type") == trigger
        and payload.get("target_id") == target_id
        and payload.get("message_mode") == "alimtalk"
        and int(payload.get("source_tenant_id") or 0) == tenant_id
        and payload.get("origin_type") == "system_account"
        and payload.get("origin_id") == target_id
    )


def _is_exact_failed_pair_outbox(
    notification: ScheduledNotification,
    *,
    tenant_id: int,
    trigger: str,
    target_id: str,
) -> bool:
    payload = notification.payload if isinstance(notification.payload, dict) else {}
    return (
        notification.tenant_id == tenant_id
        and notification.trigger == trigger
        and notification.status == ScheduledNotification.Status.FAILED
        and notification.error_message == FAILURE_REASON
        and bool(notification.dispatch_key)
        and bool(notification.business_idempotency_key)
        and notification.origin_type == ORIGINAL_ORIGIN_TYPE
        and bool(notification.origin_id)
        and payload.get("event_type") == trigger
        and payload.get("target_id") == target_id
        and payload.get("message_mode") == "alimtalk"
        and int(payload.get("source_tenant_id") or 0) == tenant_id
        and payload.get("origin_type") == notification.origin_type
        and payload.get("origin_id") == notification.origin_id
    )


def _origin_id(student_id: int) -> str:
    return f"{RECOVERY_ORIGIN_PREFIX}:{student_id}"


def _assert_queue_empty() -> None:
    try:
        client = get_queue_client(request_timeout_seconds=2)
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


def _assert_db_recovery_quiescent() -> None:
    if ScheduledNotification.objects.filter(
        status=ScheduledNotification.Status.DISPATCHING
    ).exists():
        raise CommandError("recovery_quiescence_unavailable")


def _lock_recovery_write_tables() -> None:
    """Block target-state inserts/updates during the final PostgreSQL recheck."""

    if connection.vendor != "postgresql":
        return
    table_names = (
        PendingPasswordReset._meta.db_table,
        Student._meta.db_table,
        ScheduledNotification._meta.db_table,
        NotificationLog._meta.db_table,
    )
    quoted_tables = ", ".join(
        connection.ops.quote_name(table_name) for table_name in table_names
    )
    with connection.cursor() as cursor:
        cursor.execute(
            f"LOCK TABLE {quoted_tables} "  # noqa: S608
            "IN SHARE ROW EXCLUSIVE MODE NOWAIT"
        )


def _set_recovery_lock_timeout() -> None:
    if connection.vendor != "postgresql":
        return
    with connection.cursor() as cursor:
        cursor.execute("SET LOCAL lock_timeout = '5s'")


def _is_recovery_lock_error(exc: DatabaseError) -> bool:
    cause = getattr(exc, "__cause__", None)
    sqlstate = getattr(cause, "pgcode", None) or getattr(cause, "sqlstate", None)
    return sqlstate in {"40P01", "55P03"}


@contextmanager
def _map_recovery_lock_errors(*, apply_changes: bool):
    try:
        yield
    except DatabaseError as exc:
        if apply_changes and _is_recovery_lock_error(exc):
            raise CommandError("recovery_quiescence_unavailable") from exc
        raise


def _normalize_template_body(value: object) -> str:
    return str(value or "").replace("\r\n", "\n").strip()


def _assert_rendered_envelopes(
    *,
    tenant: Tenant,
    candidates: list[RecoveryCandidate],
    templates_by_trigger: dict[str, MessageTemplate],
) -> None:
    from apps.domains.messaging.services import (
        REGISTRATION_APPROVED_NOTICE,
        get_tenant_site_url,
    )

    tenant_site_url = get_tenant_site_url(tenant)
    account_site_url = getattr(settings, "SITE_URL", "") or "https://hakwonplus.com"
    envelope_count = 0
    for candidate in candidates:
        student = candidate.student
        student_replacements = {
            "학생이름": str(student.name or ""),
            "학생아이디": str(student.ps_number or ""),
            "학생비밀번호": "__secret_preflight__",
            "사이트링크": account_site_url if candidate.mode == "student_only" else tenant_site_url,
            "비밀번호안내": (
                "로그인 정보가 변경되었습니다. 변경된 정보로 로그인해 주세요."
                if candidate.mode == "student_only"
                else REGISTRATION_APPROVED_NOTICE
            ),
        }
        envelopes = [(STUDENT_TRIGGER, student_replacements)]
        if candidate.mode == "pair":
            envelopes.append(
                (
                    PARENT_TRIGGER,
                    {
                        **student_replacements,
                        "학부모아이디": _normalize_phone(student.parent_phone),
                        "학부모비밀번호": "__secret_preflight__",
                    },
                )
            )
        for trigger, replacements in envelopes:
            template = templates_by_trigger[trigger]
            placeholders = frozenset(PLACEHOLDER_PATTERN.findall(template.body or ""))
            if placeholders != REQUIRED_PLACEHOLDERS[trigger]:
                raise CommandError(f"owner_template_placeholder_drift:{trigger}")
            rendered = str(template.body or "")
            for key, value in replacements.items():
                rendered = rendered.replace(f"#{{{key}}}", str(value))
            if not rendered.strip() or PLACEHOLDER_PATTERN.search(rendered):
                raise CommandError(f"account_notice_render_drift:{trigger}")
            envelope_count += 1
    if envelope_count != EXPECTED_OUTBOX_COUNT:
        raise CommandError("account_notice_envelope_count_mismatch")


def _assert_live_provider_contract(
    *,
    templates_by_trigger: dict[str, MessageTemplate],
) -> None:
    api_key, api_secret = _get_solapi_credentials()
    pf_id = str(resolve_kakao_channel(get_owner_tenant_id()).get("pf_id") or "").strip()
    sender = _normalize_phone(getattr(settings, "SOLAPI_SENDER", ""))
    if not api_key or not api_secret or not pf_id or len(sender) < 10:
        raise CommandError("common_alimtalk_channel_credentials_unavailable")
    try:
        active_senders = get_active_sender_numbers(api_key, api_secret)
        live_templates = list_kakao_templates(
            api_key,
            api_secret,
            pf_id,
            status_filter="APPROVED",
        )
    except Exception as exc:
        raise CommandError("live_provider_contract_unavailable") from exc
    if sender not in {_normalize_phone(value) for value in active_senders}:
        raise CommandError("common_sender_not_active")

    live_by_id = {
        str(item.get("templateId") or item.get("id") or "").strip(): item
        for item in live_templates
        if isinstance(item, dict)
    }
    for trigger, template in templates_by_trigger.items():
        template_id = str(template.solapi_template_id or "").strip()
        live = live_by_id.get(template_id)
        live_status = str(
            (live or {}).get("status")
            or (live or {}).get("inspectionStatus")
            or (live or {}).get("templateStatus")
            or ""
        ).upper()
        live_channel = str((live or {}).get("channelId") or "").strip()
        live_body = _normalize_template_body(
            (live or {}).get("content") or (live or {}).get("body")
        )
        if (
            live is None
            or live_status not in {"APPROVED", "ACTIVE"}
            or live_channel != pf_id
            or live_body != _normalize_template_body(template.body)
        ):
            raise CommandError(f"live_provider_template_drift:{trigger}")


def _assert_runtime_preflight(
    *,
    tenant: Tenant,
    candidates: list[RecoveryCandidate],
    lock: bool,
    check_external: bool = True,
) -> None:
    if not tenant.messaging_is_active:
        raise CommandError("business_tenant_messaging_inactive")
    if is_messaging_ops_held(tenant.id):
        raise CommandError("business_tenant_messaging_ops_held")

    owner_id = int(get_owner_tenant_id())
    owner_query = Tenant.objects.filter(pk=owner_id, is_active=True)
    if lock:
        owner_query = owner_query.select_for_update(
            no_key=True,
            nowait=True,
            of=("self",),
        )
    if not owner_query.exists() or is_messaging_runtime_held(owner_id):
        raise CommandError("owner_messaging_runtime_unavailable")

    config_query = AutoSendConfig.objects.filter(
        tenant_id=owner_id,
        trigger__in=ACCOUNT_TRIGGERS,
    ).order_by("trigger")
    if lock:
        config_query = config_query.select_for_update(
            no_key=True,
            nowait=True,
            of=("self",),
        )
    configs = list(config_query)
    if {config.trigger for config in configs} != set(ACCOUNT_TRIGGERS):
        raise CommandError("exact_owner_account_templates_required")

    template_ids = [config.template_id for config in configs if config.template_id]
    template_query = MessageTemplate.objects.filter(id__in=template_ids).order_by("id")
    if lock:
        template_query = template_query.select_for_update(
            no_key=True,
            nowait=True,
            of=("self",),
        )
    templates = {template.id: template for template in template_query}
    templates_by_trigger: dict[str, MessageTemplate] = {}
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
        templates_by_trigger[config.trigger] = template

    _assert_rendered_envelopes(
        tenant=tenant,
        candidates=candidates,
        templates_by_trigger=templates_by_trigger,
    )
    if not check_external:
        return
    _assert_live_provider_contract(templates_by_trigger=templates_by_trigger)

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
    owner_id = int(get_owner_tenant_id())
    base_students = Student.objects.filter(
        tenant=tenant,
        id__in=student_ids,
        deleted_at__isnull=True,
    )
    if lock:
        list(
            base_students.select_for_update(
                no_key=True,
                nowait=True,
                of=("self",),
            ).order_by("id")
        )

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
        list(
            parent_query.select_for_update(
                no_key=True,
                nowait=True,
                of=("self",),
            )
        )
    parents = list(parent_query.select_related("user"))
    parent_by_id = {parent.id: parent for parent in parents}

    linked_student_query = (
        Student.objects.filter(
            parent_id__in=parent_ids,
        )
        .select_related("user")
        .order_by("id")
    )
    if lock:
        linked_student_query = linked_student_query.select_for_update(
            no_key=True,
            nowait=True,
            of=("self",),
        )
    linked_students = list(linked_student_query)
    linked_students_by_parent: dict[int, list[Student]] = {}
    for linked_student in linked_students:
        linked_students_by_parent.setdefault(linked_student.parent_id, []).append(
            linked_student
        )

    user_ids = [student.user_id for student in students]
    user_ids.extend(parent.user_id for parent in parents if parent.user_id)
    user_ids.extend(
        linked_student.user_id
        for linked_student in linked_students
        if linked_student.user_id
    )
    user_query = get_user_model().objects.filter(id__in=user_ids).order_by("id")
    user_rows = list(
        user_query.select_for_update(
            no_key=True,
            nowait=True,
            of=("self",),
        )
        if lock
        else user_query
    )
    users = {user.id: user for user in user_rows}

    enrollment_query = Enrollment.objects.filter(
        tenant=tenant,
        student_id__in=student_ids,
        status="ACTIVE",
    ).order_by("id")
    if lock:
        list(
            enrollment_query.select_for_update(
                no_key=True,
                nowait=True,
                of=("self",),
            )
        )
    active_enrollment_counts = Counter(enrollment_query.values_list("student_id", flat=True))

    pending_query = PendingPasswordReset.objects.filter(user_id__in=user_ids).order_by("id")
    if lock:
        list(
            pending_query.select_for_update(
                no_key=True,
                nowait=True,
                of=("self",),
            )
        )
    if pending_query.exists():
        raise CommandError("pending_password_reset_exists")

    expected_targets = {
        target
        for student_id in student_ids
        for target in (f"student:{student_id}", f"parent:{student_id}")
    }
    outbox_query = ScheduledNotification.objects.filter(
        trigger__in=ACCOUNT_TRIGGERS,
        payload__target_id__in=expected_targets,
    ).order_by("created_at", "id")
    if lock:
        outbox_query = outbox_query.select_for_update(
            no_key=True,
            nowait=True,
            of=("self",),
        )
    outboxes = list(outbox_query)

    log_query = NotificationLog.objects.filter(
        notification_type__in=ACCOUNT_TRIGGERS,
        target_id__in=expected_targets,
    ).order_by("sent_at", "id")
    if lock:
        log_query = log_query.select_for_update(
            no_key=True,
            nowait=True,
            of=("self",),
        )
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
        if not student_user.has_usable_password() or not parent_user.has_usable_password():
            raise CommandError(f"account_password_unusable:student_id={student.id}")
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
        if user_display_username(student_user) != str(student.ps_number or "").strip():
            raise CommandError(f"student_account_identifier_drift:student_id={student.id}")
        if user_display_username(parent_user) != parent_phone:
            raise CommandError(f"parent_account_identifier_drift:student_id={student.id}")

        siblings = [
            linked_student
            for linked_student in linked_students_by_parent.get(parent.id, [])
            if linked_student.id != student.id
        ]
        if any(sibling.tenant_id != tenant.id for sibling in siblings):
            raise CommandError(
                f"cross_tenant_parent_sharing_drift:student_id={student.id}"
            )
        if mode == "student_only":
            if len(siblings) != 1:
                raise CommandError(
                    f"reviewed_shared_parent_drift:student_id={student.id}"
                )
            sibling_user = users.get(siblings[0].user_id)
            if (
                sibling_user is None
                or sibling_user.tenant_id != tenant.id
                or not sibling_user.is_active
                or siblings[0].deleted_at is not None
            ):
                raise CommandError(
                    f"reviewed_shared_parent_drift:student_id={student.id}"
                )
        elif siblings:
            raise CommandError(f"parent_shared_with_any_student:student_id={student.id}")
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

        student_logs = [
            log
            for log in logs
            if log.notification_type == STUDENT_TRIGGER
            and log.target_id == targets[STUDENT_TRIGGER]
        ]
        parent_logs = [
            log
            for log in logs
            if log.notification_type == PARENT_TRIGGER
            and log.target_id == targets[PARENT_TRIGGER]
        ]
        if mode == "student_only":
            exact_outboxes: dict[str, ScheduledNotification] = {}
            for trigger, target_id in targets.items():
                rows = matched[trigger]
                if len(rows) != 1 or not _is_exact_system_account_outbox(
                    rows[0],
                    tenant_id=tenant.id,
                    trigger=trigger,
                    target_id=target_id,
                ):
                    raise CommandError(
                        f"reviewed_system_account_outbox_drift:student_id={student.id}:"
                        f"trigger={trigger}"
                    )
                exact_outboxes[trigger] = rows[0]
            student_outbox_key = exact_outboxes[
                STUDENT_TRIGGER
            ].business_idempotency_key
            if any(
                _has_provider_acceptance_evidence(log)
                and not _is_exact_balance_rejection(
                    log,
                    owner_tenant_id=owner_id,
                    tenant_id=tenant.id,
                    target_id=targets[STUDENT_TRIGGER],
                    business_idempotency_key=student_outbox_key,
                )
                for log in student_logs
            ):
                raise CommandError(
                    f"provider_acceptance_evidence_exists:student_id={student.id}:"
                    f"trigger={STUDENT_TRIGGER}"
                )
            if not student_logs or any(
                not _is_exact_balance_rejection(
                    log,
                    owner_tenant_id=owner_id,
                    tenant_id=tenant.id,
                    target_id=targets[STUDENT_TRIGGER],
                    business_idempotency_key=student_outbox_key,
                )
                for log in student_logs
            ):
                raise CommandError(
                    f"reviewed_student_balance_rejection_required:student_id={student.id}"
                )
            parent_outbox_key = exact_outboxes[
                PARENT_TRIGGER
            ].business_idempotency_key
            canonical_parent_sent = [
                log
                for log in parent_logs
                if _is_exact_parent_success(
                    log,
                    owner_tenant_id=owner_id,
                    tenant_id=tenant.id,
                    target_id=targets[PARENT_TRIGGER],
                    business_idempotency_key=parent_outbox_key,
                )
            ]
            if len(canonical_parent_sent) != 1:
                raise CommandError(f"reviewed_parent_success_required:student_id={student.id}")
            canonical_parent_sent_id = canonical_parent_sent[0].id
            if any(
                log.id != canonical_parent_sent_id
                and not _is_allowed_parent_ambiguous_history(
                    log,
                    owner_tenant_id=owner_id,
                    tenant_id=tenant.id,
                    target_id=targets[PARENT_TRIGGER],
                )
                for log in parent_logs
            ):
                raise CommandError(
                    f"reviewed_parent_history_drift:student_id={student.id}"
                )
        else:
            for trigger, rows in matched.items():
                if len(rows) != 1:
                    raise CommandError(
                        f"exact_failed_outbox_pair_required:student_id={student.id}:"
                        f"trigger={trigger}:count={len(rows)}"
                    )
                row = rows[0]
                if not _is_exact_failed_pair_outbox(
                    row,
                    tenant_id=tenant.id,
                    trigger=trigger,
                    target_id=targets[trigger],
                ):
                    raise CommandError(
                        f"outbox_not_eligible:student_id={student.id}:trigger={trigger}"
                    )
            if student_logs or parent_logs:
                trigger = STUDENT_TRIGGER if student_logs else PARENT_TRIGGER
                raise CommandError(
                    f"provider_delivery_history_exists:student_id={student.id}:"
                    f"trigger={trigger}"
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

        if apply_changes:
            preflight_tenant = Tenant.objects.filter(
                id=tenant_id,
                is_active=True,
            ).first()
            if preflight_tenant is None:
                raise CommandError(f"active_tenant_not_found:{tenant_id}")
            if (
                str(options.get("confirm_tenant") or "").strip()
                != preflight_tenant.code
            ):
                raise CommandError(
                    "confirmation_required:--confirm-tenant must equal Tenant.code"
                )
            preflight_candidates = _load_candidates(
                tenant=preflight_tenant,
                student_ids=student_ids,
                lock=False,
            )
            _assert_runtime_preflight(
                tenant=preflight_tenant,
                candidates=preflight_candidates,
                lock=False,
                check_external=True,
            )

        result: dict | None = None
        with _map_recovery_lock_errors(apply_changes=apply_changes), transaction.atomic():
            if apply_changes:
                _set_recovery_lock_timeout()
                _lock_recovery_write_tables()
            tenant_query = Tenant.objects.filter(id=tenant_id, is_active=True)
            if apply_changes:
                tenant_query = tenant_query.select_for_update(
                    no_key=True,
                    nowait=True,
                    of=("self",),
                )
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
            _assert_runtime_preflight(
                tenant=tenant,
                candidates=candidates,
                lock=apply_changes,
                check_external=not apply_changes,
            )
            if apply_changes:
                _assert_db_recovery_quiescent()
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
