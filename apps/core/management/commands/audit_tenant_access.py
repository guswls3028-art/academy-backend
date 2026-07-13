"""Read-only tenant-access integrity preflight with a narrowly guarded repair mode."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Exists, OuterRef
from django.utils import timezone

from apps.core.models import TenantMembership
from apps.domains.parents.models import Parent
from apps.domains.students.models import Student


CONFIRM_PREFIX = "REPAIR_TENANT_ACCESS"


def _audit_snapshot() -> dict:
    User = get_user_model()
    active_same_tenant_membership = TenantMembership.objects.filter(
        user_id=OuterRef("pk"),
        tenant_id=OuterRef("tenant_id"),
        is_active=True,
    )
    active_student_profile = Student.objects.filter(
        user_id=OuterRef("pk"),
        tenant_id=OuterRef("tenant_id"),
        deleted_at__isnull=True,
    )
    missing_primary = list(
        User.objects.filter(
            is_active=True,
            tenant__isnull=False,
            tenant__is_active=True,
        )
        .annotate(
            has_membership=Exists(active_same_tenant_membership),
            has_active_student=Exists(active_student_profile),
        )
        .filter(has_membership=False)
        .order_by("tenant_id", "id")
        .values("id", "tenant_id", "has_active_student")
    )

    active_student_for_membership = Student.objects.filter(
        user_id=OuterRef("user_id"),
        tenant_id=OuterRef("tenant_id"),
        deleted_at__isnull=True,
    )
    other_active_membership = TenantMembership.objects.filter(
        user_id=OuterRef("user_id"),
        is_active=True,
    ).exclude(pk=OuterRef("pk"))
    missing_students = list(
        TenantMembership.objects.filter(
            role="student",
            is_active=True,
            tenant__is_active=True,
            user__is_active=True,
        )
        .annotate(
            has_active_student=Exists(active_student_for_membership),
            has_other_active_membership=Exists(other_active_membership),
        )
        .filter(has_active_student=False)
        .order_by("tenant_id", "id")
        .values(
            "id",
            "user_id",
            "tenant_id",
            "has_other_active_membership",
        )
    )

    parent_for_membership = Parent.objects.filter(
        user_id=OuterRef("user_id"),
        tenant_id=OuterRef("tenant_id"),
    )
    missing_parents = list(
        TenantMembership.objects.filter(
            role="parent",
            is_active=True,
            tenant__is_active=True,
            user__is_active=True,
        )
        .annotate(has_parent=Exists(parent_for_membership))
        .filter(has_parent=False)
        .order_by("tenant_id", "id")
        .values("id", "user_id", "tenant_id")
    )

    repair_create_user_ids = [
        row["id"] for row in missing_primary if row["has_active_student"]
    ]
    repair_deactivate_membership_ids = [
        row["id"]
        for row in missing_students
        if not row["has_other_active_membership"]
    ]
    return {
        "active_primary_users_missing_membership": missing_primary,
        "student_memberships_missing_active_student": missing_students,
        "parent_memberships_missing_parent": missing_parents,
        "repair_plan": {
            "create_student_membership_for_user_ids": repair_create_user_ids,
            "deactivate_orphan_student_membership_ids": repair_deactivate_membership_ids,
        },
    }


def _finding_count(snapshot: dict) -> int:
    return sum(
        len(snapshot[key])
        for key in (
            "active_primary_users_missing_membership",
            "student_memberships_missing_active_student",
            "parent_memberships_missing_parent",
        )
    )


def _confirmation_token(snapshot: dict) -> str:
    plan = snapshot["repair_plan"]
    digest = hashlib.sha256(
        json.dumps(
            plan,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return (
        f"{CONFIRM_PREFIX}:"
        f"{len(plan['create_student_membership_for_user_ids'])}:"
        f"{len(plan['deactivate_orphan_student_membership_ids'])}:"
        f"{digest}"
    )


class Command(BaseCommand):
    help = (
        "Audit active tenant users and limited-role memberships. Read-only by "
        "default; repair requires --execute, an exact confirmation token, and "
        "a new backup JSON path."
    )

    def add_arguments(self, parser):
        parser.add_argument("--no-fail", action="store_true")
        parser.add_argument("--execute", action="store_true")
        parser.add_argument("--confirm", default="")
        parser.add_argument("--backup-file", default="")

    def handle(self, *args, **options):
        snapshot = _audit_snapshot()
        token = _confirmation_token(snapshot)
        report = {
            "mode": "execute" if options["execute"] else "dry-run",
            "finding_count": _finding_count(snapshot),
            "counts": {
                "active_primary_users_missing_membership": len(
                    snapshot["active_primary_users_missing_membership"]
                ),
                "student_memberships_missing_active_student": len(
                    snapshot["student_memberships_missing_active_student"]
                ),
                "parent_memberships_missing_parent": len(
                    snapshot["parent_memberships_missing_parent"]
                ),
            },
            **snapshot,
            "required_confirmation_token": token,
        }
        self.stdout.write(json.dumps(report, ensure_ascii=False, sort_keys=True))

        if options["execute"]:
            remaining = self._execute(snapshot, token, options)
            self.stdout.write(
                json.dumps(
                    {
                        "mode": "post-repair-verification",
                        "finding_count": _finding_count(remaining),
                        **remaining,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
            return

        if _finding_count(snapshot) and not options["no_fail"]:
            raise CommandError("tenant access integrity findings detected")

    def _execute(self, snapshot: dict, token: str, options: dict) -> dict:
        if options["confirm"] != token:
            raise CommandError(f"exact --confirm value required: {token}")
        if not options["backup_file"]:
            raise CommandError("--backup-file is required for repair")
        raw_backup_path = Path(options["backup_file"]).expanduser()
        if not raw_backup_path.is_absolute():
            raise CommandError("--backup-file must be an absolute durable path")
        backup_path = raw_backup_path.resolve()
        if not backup_path.parent.is_dir():
            raise CommandError("backup parent directory does not exist")

        plan = snapshot["repair_plan"]
        if (
            len(plan["create_student_membership_for_user_ids"])
            != len(snapshot["active_primary_users_missing_membership"])
            or len(plan["deactivate_orphan_student_membership_ids"])
            != len(snapshot["student_memberships_missing_active_student"])
            or snapshot["parent_memberships_missing_parent"]
        ):
            raise CommandError(
                "repair plan does not cover every finding; no changes were applied"
            )

        user_ids = plan["create_student_membership_for_user_ids"]
        membership_ids = plan["deactivate_orphan_student_membership_ids"]
        affected_user_ids = set(user_ids)
        affected_user_ids.update(
            TenantMembership.objects.filter(id__in=membership_ids)
            .values_list("user_id", flat=True)
        )

        User = get_user_model()
        with transaction.atomic():
            list(
                User.objects.select_for_update()
                .filter(id__in=affected_user_ids)
                .order_by("id")
            )
            current = _audit_snapshot()
            if current["repair_plan"] != plan:
                raise CommandError("repair candidates changed since dry-run; rerun audit")

            backup = {
                "schema_version": 1,
                "created_at": timezone.now().isoformat(),
                "confirmation_token": token,
                "plan": plan,
                "users_before": list(
                    User.objects.filter(id__in=affected_user_ids)
                    .order_by("id")
                    .values("id", "tenant_id", "is_active", "is_staff", "token_version")
                ),
                "memberships_before": list(
                    TenantMembership.objects.filter(user_id__in=affected_user_ids)
                    .order_by("id")
                    .values("id", "user_id", "tenant_id", "role", "is_active")
                ),
            }
            try:
                with backup_path.open("x", encoding="utf-8") as backup_file:
                    json.dump(
                        backup,
                        backup_file,
                        ensure_ascii=False,
                        indent=2,
                        sort_keys=True,
                    )
                    backup_file.flush()
                    os.fsync(backup_file.fileno())
            except FileExistsError as exc:
                raise CommandError(
                    "backup file already exists; refusing to overwrite"
                ) from exc
            except OSError as exc:
                raise CommandError(f"backup file could not be persisted: {exc}") from exc

            from academy.adapters.db.django import repositories_core as core_repo
            from apps.core.services.tenant_access import deactivate_tenant_membership

            for user in User.objects.filter(id__in=user_ids).select_related("tenant").order_by("id"):
                core_repo.membership_ensure_active(
                    tenant=user.tenant,
                    user=user,
                    role="student",
                )
            for membership in (
                TenantMembership.objects.filter(id__in=membership_ids)
                .select_related("tenant", "user")
                .order_by("id")
            ):
                deactivate_tenant_membership(
                    user=membership.user,
                    tenant=membership.tenant,
                    allowed_roles=("student",),
                )
            remaining = _audit_snapshot()
            if _finding_count(remaining):
                raise CommandError(
                    "post-repair verification failed; database changes were rolled back"
                )
            return remaining
