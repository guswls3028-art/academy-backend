# PATH: apps/domains/submissions/migrations/0005_backfill_submissions_tenant.py
from django.db import migrations


def backfill_submissions_tenant(apps, schema_editor):
    Tenant = apps.get_model("core", "Tenant")
    Submission = apps.get_model("submissions", "Submission")
    SubmissionAnswer = apps.get_model("submissions", "SubmissionAnswer")

    tenants = Tenant.objects.filter(is_active=True).order_by("id")
    if tenants.count() != 1:
        raise RuntimeError("Cannot auto-backfill tenant for submissions domain")

    tenant = tenants.first()
    Submission.objects.filter(tenant__isnull=True).update(tenant=tenant)
    SubmissionAnswer.objects.filter(tenant__isnull=True).update(tenant=tenant)


class Migration(migrations.Migration):

    dependencies = [
        ("submissions", "0004_add_submissions_tenant"),
    ]

    operations = [
        migrations.RunPython(backfill_submissions_tenant, migrations.RunPython.noop),
    ]
