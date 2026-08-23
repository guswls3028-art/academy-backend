"""Repair rows where a parent phone was copied into the student phone field."""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import F, Q

from apps.core.models import Tenant
from apps.domains.students.models import Student
from apps.domains.students.services.identity import derive_student_omr_code, phone_digits
from apps.support.students.account_notice_dependencies import (
    active_student_account_outbox_exists,
)


def _parse_student_ids(raw: str) -> list[int]:
    values = [item.strip() for item in str(raw or "").split(",") if item.strip()]
    try:
        student_ids = sorted({int(item) for item in values})
    except ValueError as exc:
        raise CommandError("student_ids_invalid:use_comma_separated_integers") from exc
    if any(student_id <= 0 for student_id in student_ids):
        raise CommandError("student_ids_invalid:positive_integers_required")
    return student_ids


def _candidate_queryset(*, tenant: Tenant):
    return (
        Student.objects.filter(
            tenant=tenant,
            deleted_at__isnull=True,
            phone=F("parent_phone"),
        )
        .exclude(Q(phone__isnull=True) | Q(phone=""))
        .select_related("user", "parent__user")
        .order_by("id")
    )


class Command(BaseCommand):
    help = (
        "Dry-run by default. Clear copied parent phones from active student "
        "contacts without changing login IDs, passwords, or parent accounts."
    )

    def add_arguments(self, parser):
        parser.add_argument("--tenant", required=True, help="Exact active Tenant.code")
        parser.add_argument(
            "--student-ids",
            default="",
            help="Comma-separated exact candidate IDs; required for --execute",
        )
        parser.add_argument("--execute", action="store_true")
        parser.add_argument(
            "--confirm",
            default="",
            help="For --execute, repeat '<tenant>:<candidate-count>'",
        )

    def handle(self, *args, **options):
        tenant_code = str(options["tenant"] or "").strip()
        requested_ids = _parse_student_ids(options.get("student_ids") or "")
        execute = bool(options["execute"])

        tenant = Tenant.objects.filter(code=tenant_code, is_active=True).first()
        if tenant is None:
            raise CommandError(f"tenant_not_found_or_inactive:{tenant_code}")

        candidates = list(_candidate_queryset(tenant=tenant))
        candidate_ids = [student.id for student in candidates]
        mode = "execute" if execute else "dry-run"
        self.stdout.write(
            f"shared_student_parent_phone tenant={tenant.code} mode={mode} "
            f"candidates={len(candidate_ids)} ids={','.join(map(str, candidate_ids)) or '-'}"
        )

        if not execute:
            return
        if not requested_ids:
            raise CommandError("student_ids_required_for_execute")
        if requested_ids != candidate_ids:
            raise CommandError("candidate_set_mismatch:rerun_dry_run")
        if str(options.get("confirm") or "").strip() != f"{tenant.code}:{len(candidate_ids)}":
            raise CommandError("confirmation_required:use_<tenant>:<candidate-count>")

        with transaction.atomic():
            Tenant.objects.select_for_update().get(pk=tenant.pk)
            locked_candidates = list(
                _candidate_queryset(tenant=tenant).select_for_update(of=("self",))
            )
            if [student.id for student in locked_candidates] != requested_ids:
                raise CommandError("candidate_set_changed:rerun_dry_run")

            for student in locked_candidates:
                self._assert_safe_candidate(student)
                student.phone = None
                student.uses_identifier = True
                student.omr_code = derive_student_omr_code(
                    phone=None,
                    parent_phone=student.parent_phone,
                )
                student.save(
                    update_fields=["phone", "uses_identifier", "omr_code", "updated_at"]
                )
                student.user.phone = None
                student.user.save(update_fields=["phone"])

            if _candidate_queryset(tenant=tenant).exists():
                raise CommandError("post_state_shared_phone_rows_remain")

        self.stdout.write(
            self.style.SUCCESS(
                f"shared_student_parent_phone_complete tenant={tenant.code} "
                f"repaired={len(requested_ids)} login_ids_changed=0 passwords_changed=0 "
                "parent_accounts_changed=0 notifications_sent=0"
            )
        )

    @staticmethod
    def _assert_safe_candidate(student: Student) -> None:
        shared_phone = phone_digits(student.phone)
        if not shared_phone or shared_phone != phone_digits(student.parent_phone):
            raise CommandError(f"candidate_phone_changed:student_id={student.id}")
        if not student.user_id or phone_digits(student.user.phone) != shared_phone:
            raise CommandError(f"student_user_phone_drift:student_id={student.id}")
        if not student.parent_id or not student.parent.user_id:
            raise CommandError(f"parent_account_missing:student_id={student.id}")
        if phone_digits(student.parent.phone) != shared_phone:
            raise CommandError(f"parent_phone_drift:student_id={student.id}")
        if (
            student.pending_account_notice_student_password_ciphertext
            or student.pending_account_notice_parent_password_ciphertext
        ):
            raise CommandError(f"pending_account_notice_exists:student_id={student.id}")

        if active_student_account_outbox_exists(
            tenant_id=student.tenant_id,
            student_id=student.id,
        ):
            raise CommandError(f"active_account_outbox_exists:student_id={student.id}")
