from __future__ import annotations

import hashlib
import io
import json
import uuid

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
import pdfplumber

from apps.domains.landing_public.models import PublicProblemReviewShowcase
from apps.domains.tools.contracts import (
    problem_review_report_fingerprint,
    render_problem_review_report,
)
from apps.infrastructure.storage.r2 import (
    delete_object_r2_storage,
    get_object_bytes_r2_storage,
    upload_fileobj_to_r2_storage,
)


COMPATIBILITY_MARKER = "pre-verification-publication"


def _pdf_identity(data: bytes) -> dict[str, str | int]:
    with pdfplumber.open(io.BytesIO(data)) as document:
        pages = len(document.pages)
    return {
        "bytes": len(data),
        "pages": pages,
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def _is_repairable(showcase: PublicProblemReviewShowcase) -> bool:
    snapshot = showcase.snapshot if isinstance(showcase.snapshot, dict) else {}
    verification = snapshot.get("verification")
    return bool(
        showcase.status == PublicProblemReviewShowcase.Status.PUBLISHED
        and showcase.published_at
        and showcase.snapshot_at
        and isinstance(verification, dict)
        and verification.get("status") == "legacy_published"
        and verification.get("compatibility") == COMPATIBILITY_MARKER
    )


def repair_showcase_pdf(showcase: PublicProblemReviewShowcase, *, apply: bool) -> dict:
    if not _is_repairable(showcase):
        raise CommandError(
            f"showcase {showcase.pk} is not an exact legacy publication compatibility row"
        )
    old_key = showcase.snapshot_pdf_key
    if not old_key:
        raise CommandError(f"showcase {showcase.pk} has no published PDF key")
    old_bytes = get_object_bytes_r2_storage(
        key=old_key,
        max_bytes=20 * 1024 * 1024,
        timeout_seconds=30,
    )
    if old_bytes is None:
        raise CommandError(f"showcase {showcase.pk} published PDF is missing")

    # The legacy verification marker is intentionally passed without export meta.
    # The renderer therefore prints "최종 검수 증표 없음" instead of inventing a
    # completed_at timestamp or report fingerprint.
    _, snapshot_sha256 = problem_review_report_fingerprint(showcase.snapshot)
    new_bytes, _, _ = render_problem_review_report(
        {
            **showcase.snapshot,
            "_export_meta": {
                "identity_kind": "legacy_publication",
                "identity_label": snapshot_sha256[:12],
            },
        },
        output_format="pdf",
    )
    old_identity = _pdf_identity(old_bytes)
    new_identity = _pdf_identity(new_bytes)
    result = {
        "showcase_id": showcase.pk,
        "tenant_id": showcase.tenant_id,
        "old_key": old_key,
        "old": old_identity,
        "new": new_identity,
        "snapshot_sha256": snapshot_sha256,
        "applied": False,
    }
    if not apply:
        return result

    new_key = (
        f"problem-review-showcase-snapshots/tenant_{showcase.tenant_id}/"
        f"{showcase.report_id_ref}/legacy-layout-repair-{uuid.uuid4().hex}.pdf"
    )
    try:
        upload_fileobj_to_r2_storage(
            fileobj=io.BytesIO(new_bytes),
            key=new_key,
            content_type="application/pdf",
            timeout_seconds=30,
        )
        uploaded_bytes = get_object_bytes_r2_storage(
            key=new_key,
            max_bytes=20 * 1024 * 1024,
            timeout_seconds=30,
        )
        if uploaded_bytes is None or _pdf_identity(uploaded_bytes) != new_identity:
            raise CommandError(
                f"showcase {showcase.pk} replacement PDF failed R2 identity readback"
            )
        with transaction.atomic():
            current = PublicProblemReviewShowcase.objects.select_for_update().get(pk=showcase.pk)
            if current.snapshot_pdf_key != old_key or not _is_repairable(current):
                raise CommandError(
                    f"showcase {showcase.pk} changed while its replacement PDF was rendered"
                )
            current.snapshot_pdf_key = new_key
            current.snapshot_pdf_bytes = len(new_bytes)
            current.save(update_fields=["snapshot_pdf_key", "snapshot_pdf_bytes", "updated_at"])
    except Exception:
        try:
            delete_object_r2_storage(key=new_key, timeout_seconds=10)
        except Exception:
            pass
        raise

    result["new_key"] = new_key
    result["applied"] = True
    return result


class Command(BaseCommand):
    help = "Re-render exact legacy problem-review publications without changing review meaning."

    def add_arguments(self, parser):
        parser.add_argument("--tenant-code", required=True)
        parser.add_argument("--showcase-id", action="append", required=True, type=int)
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Upload a new PDF and atomically replace the row's object key.",
        )

    def handle(self, *args, **options):
        tenant_code = str(options["tenant_code"]).strip()
        showcase_ids = list(dict.fromkeys(options["showcase_id"]))
        showcases = {
            item.pk: item
            for item in PublicProblemReviewShowcase.objects.select_related("tenant").filter(
                tenant__code=tenant_code,
                pk__in=showcase_ids,
            )
        }
        missing = [showcase_id for showcase_id in showcase_ids if showcase_id not in showcases]
        if missing:
            raise CommandError(
                f"showcases not found in tenant {tenant_code}: {','.join(map(str, missing))}"
            )

        for showcase_id in showcase_ids:
            result = repair_showcase_pdf(showcases[showcase_id], apply=options["apply"])
            self.stdout.write(json.dumps(result, ensure_ascii=False, sort_keys=True))
