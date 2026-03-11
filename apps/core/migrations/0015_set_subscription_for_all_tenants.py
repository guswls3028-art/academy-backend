# Data migration: Set subscription for all tenants
# - Tenants 1, 2, 9999: Premium plan, 9999 days
# - All others: Basic plan, March 13 2026 start, 1 month (April 12 2026)

from datetime import date, timedelta
from django.db import migrations


def set_subscriptions(apps, schema_editor):
    Program = apps.get_model("core", "Program")

    premium_tenant_ids = {1, 2, 9999}
    start_date = date(2026, 3, 13)

    for program in Program.objects.select_related("tenant").all():
        tid = program.tenant_id
        if tid in premium_tenant_ids:
            program.plan = "premium"
            program.monthly_price = 300_000
            program.subscription_status = "active"
            program.subscription_started_at = start_date
            program.subscription_expires_at = start_date + timedelta(days=9999)
        else:
            program.plan = "basic"
            program.monthly_price = 150_000
            program.subscription_status = "active"
            program.subscription_started_at = start_date
            program.subscription_expires_at = date(2026, 4, 12)
        program.save(update_fields=[
            "plan", "monthly_price",
            "subscription_status", "subscription_started_at", "subscription_expires_at",
        ])


def reverse_subscriptions(apps, schema_editor):
    Program = apps.get_model("core", "Program")
    Program.objects.all().update(
        subscription_status="active",
        subscription_started_at=None,
        subscription_expires_at=None,
    )


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0014_program_billing_email_and_more"),
    ]

    operations = [
        migrations.RunPython(set_subscriptions, reverse_subscriptions),
    ]
