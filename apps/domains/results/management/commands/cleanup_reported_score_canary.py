"""Remove only fully detached, terminal student-score canary audit rows."""

from __future__ import annotations

import re

from django.core.management.base import BaseCommand, CommandError

from apps.core.models import Tenant
from apps.domains.results.models import StudentReportedScore
from apps.support.results.student_reported_scores import recover_reported_score_canary


MARKER_PATTERN = re.compile(r"^\[E2E-\d{14}-[0-9a-f]{8}\]$")


class Command(BaseCommand):
    help = "Recover and delete an exact UUID-suffixed student-score canary marker."

    def add_arguments(self, parser):
        parser.add_argument("--tenant-code", default="hakwonplus")
        parser.add_argument("--marker", required=True)
        parser.add_argument("--confirm", default="")
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--recover-active", action="store_true")
        parser.add_argument("--wait-seconds", type=int, default=20)

    def handle(self, *args, **options):
        marker = str(options["marker"] or "").strip()
        if not MARKER_PATTERN.fullmatch(marker):
            raise CommandError("marker must exactly match [E2E-YYYYMMDDHHMMSS-xxxxxxxx]")
        if not options["dry_run"] and options["confirm"] != marker:
            raise CommandError("--confirm must exactly match --marker")

        tenant = Tenant.objects.filter(code=options["tenant_code"]).first()
        if not tenant:
            raise CommandError("tenant not found")

        def marker_rows():
            return StudentReportedScore.objects.filter(
                tenant=tenant,
                exam_name=marker,
                subject__startswith=marker,
            )

        if options["dry_run"]:
            matched = marker_rows()
            active_count = matched.filter(
                status__in=(
                    StudentReportedScore.Status.PENDING,
                    StudentReportedScore.Status.VERIFIED,
                )
            ).count()
            evidence_count = matched.exclude(evidence_file__isnull=True).values(
                "evidence_file_id"
            ).distinct().count()
            detached_count = matched.filter(
                evidence_file__isnull=True,
                status__in=(
                    StudentReportedScore.Status.REJECTED,
                    StudentReportedScore.Status.VOIDED,
                ),
            ).count()
            self.stdout.write(
                "DRY RUN "
                f"marker={marker} rows={matched.count()} active={active_count} "
                f"evidence={evidence_count} detached={detached_count}"
            )
            return

        if options["recover_active"]:
            try:
                recover_reported_score_canary(
                    tenant=tenant,
                    marker=marker,
                    wait_seconds=int(options["wait_seconds"]),
                )
            except ValueError as exc:
                raise CommandError(str(exc)) from exc

        rows = marker_rows().filter(
            evidence_file__isnull=True,
            status__in=(
                StudentReportedScore.Status.REJECTED,
                StudentReportedScore.Status.VOIDED,
            ),
        )
        count = rows.count()
        deleted, _details = rows.delete()
        if deleted != count:
            raise CommandError(f"unexpected cascade: matched={count} deleted={deleted}")
        self.stdout.write(self.style.SUCCESS(f"CLEANED marker={marker} rows={count}"))
