from __future__ import annotations

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.core.serializers.json import DjangoJSONEncoder

from apps.core.product_analytics.pilot import (
    build_pilot_report,
    disable_pilot_on_hard_breach,
)


class Command(BaseCommand):
    help = "Report and fail closed on exact product-analytics pilot gates."

    def add_arguments(self, parser):
        parser.add_argument("--tenant-code", required=True)
        parser.add_argument("--window-days", type=int, default=28)
        parser.add_argument("--projection-days", type=int, default=90)
        parser.add_argument("--max-projected-database-share", type=float, default=0.20)
        parser.add_argument("--db-time-share", type=float)
        parser.add_argument("--write-share", type=float)
        parser.add_argument("--max-db-time-share", type=float, default=0.10)
        parser.add_argument("--max-write-share", type=float, default=0.10)
        parser.add_argument("--disable-on-hard-breach", action="store_true")
        parser.add_argument("--confirm", default="")
        parser.add_argument("--output")

    def handle(self, *args, **options):
        tenant_code = options["tenant_code"].strip().lower()
        if not tenant_code or tenant_code != options["tenant_code"]:
            raise CommandError("--tenant-code must be normalized lowercase")
        if options["window_days"] not in (7, 28, 90):
            raise CommandError("--window-days must be 7, 28, or 90")
        if options["projection_days"] < options["window_days"]:
            raise CommandError("--projection-days must not be shorter than the window")
        for option in (
            "max_projected_database_share",
            "max_db_time_share",
            "max_write_share",
        ):
            if not 0 < options[option] <= 1:
                raise CommandError(f"--{option.replace('_', '-')} must be in (0, 1]")
        for option in ("db_time_share", "write_share"):
            value = options.get(option)
            if value is not None and not 0 <= value <= 1:
                raise CommandError(f"--{option.replace('_', '-')} must be in [0, 1]")

        expected_confirmation = f"DISABLE {tenant_code} ON HARD BREACH"
        if options["disable_on_hard_breach"] and options["confirm"] != expected_confirmation:
            raise CommandError(
                "--disable-on-hard-breach requires the exact documented confirmation"
            )

        report = build_pilot_report(
            tenant_code=tenant_code,
            window_days=options["window_days"],
            projection_days=options["projection_days"],
            max_projected_database_share=options[
                "max_projected_database_share"
            ],
            db_time_share=options.get("db_time_share"),
            write_share=options.get("write_share"),
            max_db_time_share=options["max_db_time_share"],
            max_write_share=options["max_write_share"],
        )
        disabled = False
        if options["disable_on_hard_breach"]:
            disabled = disable_pilot_on_hard_breach(
                tenant_code=tenant_code,
                report=report,
            )
        report["failsafe_disabled"] = disabled

        encoded = json.dumps(
            report,
            cls=DjangoJSONEncoder,
            ensure_ascii=False,
            sort_keys=True,
        )
        if options.get("output"):
            path = Path(options["output"])
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(encoded + "\n", encoding="utf-8")
        self.stdout.write(f"PRODUCT_ANALYTICS_PILOT_REPORT {encoded}")

        if report["breaches"]:
            raise CommandError(
                "product analytics pilot gate breached: "
                + ", ".join(report["breaches"])
            )
        self.stdout.write(
            self.style.SUCCESS(
                f"PRODUCT_ANALYTICS_PILOT_PASS tenant={tenant_code} "
                f"observed_days={report['period']['observed_days']}"
            )
        )
