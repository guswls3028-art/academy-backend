"""Prepare static JPEGs before public matchup pages receive traffic."""

from __future__ import annotations

from django.apps import apps
from django.core.management.base import BaseCommand, CommandError

from apps.core.models import LandingPage, Tenant
from apps.domains.matchup.models import MatchupHitReport
from apps.domains.matchup.views_hit_report import _get_or_generate_hit_report_preview
from apps.infrastructure.storage.r2 import get_object_bytes_r2_storage
from apps.support.landing_public.matchup_preview import get_or_create_matchup_preview


def _published_landing_report_ids(tenant) -> set[int]:
    landing = LandingPage.objects.filter(tenant=tenant, is_published=True).first()
    if landing is None:
        return set()

    report_ids: set[int] = set()
    for section in (landing.published_config or {}).get("sections") or []:
        if section.get("type") != "hit_reports" or not section.get("enabled"):
            continue
        for item in section.get("items") or []:
            try:
                report_ids.add(int(item.get("report_id")))
            except (AttributeError, TypeError, ValueError):
                continue
    return report_ids


class Command(BaseCommand):
    help = (
        "Prewarm immutable matchup JPEG previews for one tenant without "
        "modifying report or landing-page rows."
    )

    def add_arguments(self, parser):
        parser.add_argument("--tenant-code", required=True)
        parser.add_argument(
            "--include-share-links",
            action="store_true",
            help="Also prewarm reports with an active public share token.",
        )

    def handle(self, *args, **options):
        tenant_code = str(options["tenant_code"]).strip()
        tenant = Tenant.objects.filter(code=tenant_code, is_active=True).first()
        if tenant is None:
            raise CommandError(f"Active tenant not found: {tenant_code}")

        report_ids = _published_landing_report_ids(tenant)
        if options["include_share_links"]:
            report_ids.update(
                MatchupHitReport.objects.filter(
                    tenant=tenant,
                    share_token__isnull=False,
                ).values_list("id", flat=True),
            )

        prepared_reports = 0
        errors: list[str] = []
        reports = {
            report.id: report
            for report in MatchupHitReport.objects.select_related(
                "document",
                "author",
            ).filter(tenant=tenant, id__in=report_ids)
        }
        for report_id in sorted(report_ids):
            report = reports.get(report_id)
            if report is None:
                errors.append(f"legacy report {report_id}: not found")
                continue
            try:
                _get_or_generate_hit_report_preview(
                    report,
                    require_cache_write=True,
                )
                prepared_reports += 1
            except Exception as exc:
                errors.append(f"legacy report {report_id}: {type(exc).__name__}")

        prepared_showcases = 0
        public_matchup_showcase = apps.get_model(
            "landing_public",
            "PublicMatchupShowcase",
        )
        showcases = public_matchup_showcase.objects.filter(
            tenant=tenant,
            status=public_matchup_showcase.Status.PUBLISHED,
        ).exclude(snapshot_pdf_key="")
        for showcase in showcases.iterator():
            pdf_key = showcase.snapshot_pdf_key

            def load_pdf_bytes(*, key=pdf_key):
                data = get_object_bytes_r2_storage(key=key)
                if data is None:
                    raise FileNotFoundError(key)
                return data

            try:
                source = str((showcase.snapshot_meta or {}).get("source") or "")
                get_or_create_matchup_preview(
                    pdf_key=pdf_key,
                    load_pdf_bytes=load_pdf_bytes,
                    first_body_page=not source.startswith("user_upload"),
                    require_cache_write=True,
                )
                prepared_showcases += 1
            except Exception as exc:
                errors.append(f"showcase {showcase.id}: {type(exc).__name__}")

        self.stdout.write(
            self.style.SUCCESS(
                f"tenant={tenant.code} legacy={prepared_reports} "
                f"showcase={prepared_showcases}",
            ),
        )
        if errors:
            raise CommandError("; ".join(errors))
