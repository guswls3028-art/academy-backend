from django.db import migrations
from django.utils import timezone


COMPATIBILITY_MARKER = "pre-verification-publication"


def mark_legacy_publications(apps, schema_editor):
    PublicProblemReviewShowcase = apps.get_model(
        "landing_public",
        "PublicProblemReviewShowcase",
    )
    migrated_at = timezone.now().isoformat()
    queryset = PublicProblemReviewShowcase.objects.filter(
        status="published",
        published_at__isnull=False,
        snapshot_at__isnull=False,
    )
    for showcase in queryset.iterator():
        snapshot = showcase.snapshot if isinstance(showcase.snapshot, dict) else {}
        if snapshot.get("verification") not in (None, {}):
            continue
        snapshot = dict(snapshot)
        snapshot["verification"] = {
            "status": "legacy_published",
            "compatibility": COMPATIBILITY_MARKER,
            "published_at": showcase.published_at.isoformat(),
            "migrated_at": migrated_at,
        }
        PublicProblemReviewShowcase.objects.filter(pk=showcase.pk).update(snapshot=snapshot)


def unmark_legacy_publications(apps, schema_editor):
    PublicProblemReviewShowcase = apps.get_model(
        "landing_public",
        "PublicProblemReviewShowcase",
    )
    queryset = PublicProblemReviewShowcase.objects.filter(
        snapshot__verification__status="legacy_published",
    )
    for showcase in queryset.iterator():
        snapshot = showcase.snapshot if isinstance(showcase.snapshot, dict) else {}
        verification = snapshot.get("verification")
        if not isinstance(verification, dict):
            continue
        if verification.get("compatibility") != COMPATIBILITY_MARKER:
            continue
        snapshot = dict(snapshot)
        snapshot.pop("verification", None)
        PublicProblemReviewShowcase.objects.filter(pk=showcase.pk).update(snapshot=snapshot)


class Migration(migrations.Migration):
    dependencies = [
        ("landing_public", "0007_publicproblemreviewshowcase"),
    ]

    operations = [
        migrations.RunPython(
            mark_legacy_publications,
            reverse_code=unmark_legacy_publications,
        ),
    ]
