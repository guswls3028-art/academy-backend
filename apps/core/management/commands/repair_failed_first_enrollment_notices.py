"""Recover first-enrollment credentials lost to the tenant messaging kill switch.

The command is intentionally narrow and dry-run by default.  It only repairs an
exact student list when the original student and parent outboxes both failed for
the historical ``business_tenant_messaging_disabled`` reason and neither account
has ever logged in.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.core.models import Tenant
from apps.core.services.password import (
    clear_pending_password_reset,
    force_reset_password,
    generate_temp_password,
)
from apps.domains.messaging.models import NotificationLog, ScheduledNotification
from apps.domains.parents.models import Parent
from apps.domains.students.models import Student
from apps.domains.students.services.account_notice import (
    dispatch_pending_account_notice,
    stage_pending_account_notice,
)


FAILURE_REASON = "business_tenant_messaging_disabled"
ORIGINAL_ORIGIN_TYPE = "excel_import"
STUDENT_TRIGGER = "registration_approved_student"
PARENT_TRIGGER = "registration_approved_parent"


@dataclass(frozen=True)
class RecoveryCandidate:
    student: Student
    student_outbox: ScheduledNotification
    parent_outbox: ScheduledNotification


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
    return sorted(parsed)


def _payload_target(notification: ScheduledNotification) -> str:
    payload = notification.payload if isinstance(notification.payload, dict) else {}
    return str(payload.get("target_id") or "").strip()


def _load_candidates(*, tenant: Tenant, student_ids: list[int], lock: bool) -> list[RecoveryCandidate]:
    student_query = Student.objects.filter(
        tenant=tenant,
        id__in=student_ids,
        deleted_at__isnull=True,
    )
    if lock:
        locked_students = list(student_query.select_for_update().order_by("id"))
        parent_ids = [student.parent_id for student in locked_students if student.parent_id]
        user_ids = [student.user_id for student in locked_students]
        locked_parents = list(
            Parent.objects.select_for_update().filter(id__in=parent_ids).order_by("id")
        )
        user_ids.extend(parent.user_id for parent in locked_parents)
        list(
            get_user_model().objects.select_for_update()
            .filter(id__in=[user_id for user_id in user_ids if user_id])
            .order_by("id")
        )

    students = list(
        student_query.select_related("user", "parent__user").order_by("id")
    )
    found_ids = [student.id for student in students]
    if found_ids != student_ids:
        missing = sorted(set(student_ids) - set(found_ids))
        raise CommandError(f"active_students_not_found:{','.join(map(str, missing))}")

    parent_user_ids: list[int] = []
    for student in students:
        if student.user_id is None or student.user.tenant_id != tenant.id:
            raise CommandError(f"student_user_tenant_mismatch:student_id={student.id}")
        if not student.user.is_active:
            raise CommandError(f"student_user_inactive:student_id={student.id}")
        if student.parent_id is None or student.parent.user_id is None:
            raise CommandError(f"parent_account_missing:student_id={student.id}")
        if student.parent.tenant_id != tenant.id or student.parent.user.tenant_id != tenant.id:
            raise CommandError(f"parent_user_tenant_mismatch:student_id={student.id}")
        if not student.parent.user.is_active:
            raise CommandError(f"parent_user_inactive:student_id={student.id}")
        if student.user.last_login is not None or student.parent.user.last_login is not None:
            raise CommandError(f"account_already_used:student_id={student.id}")
        if (
            student.pending_account_notice_student_password_ciphertext
            or student.pending_account_notice_parent_password_ciphertext
            or student.pending_account_notice_since is not None
        ):
            raise CommandError(f"pending_account_notice_exists:student_id={student.id}")
        parent_user_ids.append(student.parent.user_id)

    if len(parent_user_ids) != len(set(parent_user_ids)):
        raise CommandError("shared_parent_account_in_student_selection")

    relevant_outboxes = list(
        ScheduledNotification.objects.filter(
            tenant=tenant,
            trigger__in=[STUDENT_TRIGGER, PARENT_TRIGGER],
        ).order_by("created_at", "id")
    )
    candidates: list[RecoveryCandidate] = []
    for student in students:
        expected_targets = {
            STUDENT_TRIGGER: f"student:{student.id}",
            PARENT_TRIGGER: f"parent:{student.id}",
        }
        matched: dict[str, list[ScheduledNotification]] = {
            STUDENT_TRIGGER: [],
            PARENT_TRIGGER: [],
        }
        for notification in relevant_outboxes:
            expected_target = expected_targets.get(notification.trigger)
            if expected_target and _payload_target(notification) == expected_target:
                matched[notification.trigger].append(notification)

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
            if NotificationLog.objects.filter(
                tenant=tenant,
                success=True,
                status="sent",
                notification_type=trigger,
                target_id=expected_targets[trigger],
                sent_at__gte=row.created_at,
            ).exists():
                raise CommandError(
                    f"later_success_already_exists:student_id={student.id}:trigger={trigger}"
                )

        candidates.append(
            RecoveryCandidate(
                student=student,
                student_outbox=matched[STUDENT_TRIGGER][0],
                parent_outbox=matched[PARENT_TRIGGER][0],
            )
        )
    return candidates


class Command(BaseCommand):
    help = (
        "Dry-run by default. Rotate never-used student/parent credentials and recreate "
        "an exact pair of first-enrollment Alimtalk outboxes that were terminally "
        "failed by the historical tenant messaging kill switch."
    )

    def add_arguments(self, parser):
        parser.add_argument("--tenant-id", type=int, required=True)
        parser.add_argument(
            "--student-ids",
            required=True,
            help="Exact comma-separated student IDs",
        )
        parser.add_argument("--apply", action="store_true")
        parser.add_argument(
            "--confirm-tenant",
            default="",
            help="For --apply, repeat the exact Tenant.code",
        )

    def handle(self, *args, **options):
        tenant_id = int(options["tenant_id"])
        student_ids = _parse_student_ids(options["student_ids"])
        apply_changes = bool(options["apply"])
        tenant = Tenant.objects.filter(id=tenant_id, is_active=True).first()
        if tenant is None:
            raise CommandError(f"active_tenant_not_found:{tenant_id}")
        if apply_changes and str(options.get("confirm_tenant") or "").strip() != tenant.code:
            raise CommandError("confirmation_required:--confirm-tenant must equal Tenant.code")

        candidates = _load_candidates(
            tenant=tenant,
            student_ids=student_ids,
            lock=False,
        )
        mode = "apply" if apply_changes else "dry-run"
        self.stdout.write(
            f"first_enrollment_notice_recovery tenant_id={tenant.id} tenant={tenant.code} "
            f"mode={mode} candidates={len(candidates)} student_ids="
            f"{','.join(str(candidate.student.id) for candidate in candidates)} "
            "account_last_login=none outbox_pairs=exact"
        )
        if not apply_changes:
            return

        dispatch_results: list[dict] = []
        with transaction.atomic():
            locked_candidates = _load_candidates(
                tenant=tenant,
                student_ids=student_ids,
                lock=True,
            )
            for candidate in locked_candidates:
                student = candidate.student
                student_password = generate_temp_password()
                parent_password = generate_temp_password()
                force_reset_password(student.user, student_password)
                force_reset_password(student.parent.user, parent_password)
                clear_pending_password_reset(student.user)
                clear_pending_password_reset(student.parent.user)
                stage_pending_account_notice(
                    student=student,
                    student_password=student_password,
                    parent_password=parent_password,
                    origin_type="account_notice_recovery",
                    origin_id=(
                        f"disabled-outboxes:{candidate.student_outbox.id},"
                        f"{candidate.parent_outbox.id}"
                    ),
                )
                result = dispatch_pending_account_notice(student_id=student.id)
                if result.get("status") != "enqueued" or result.get("enqueued") != 2:
                    raise CommandError(
                        f"replacement_outbox_pair_not_created:student_id={student.id}"
                    )
                dispatch_results.append(result)

        self.stdout.write(
            self.style.SUCCESS(
                f"first_enrollment_notice_recovery_complete tenant_id={tenant.id} "
                f"students={len(candidates)} credentials_rotated={len(candidates) * 2} "
                f"outboxes_enqueued={sum(result['enqueued'] for result in dispatch_results)}"
            )
        )
