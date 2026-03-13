"""Rename plan values: lite→standard, basic→pro, premium→max.
Set promo pricing for former-basic tenants (now pro) at 150,000.
Tenants 1/2/9999 → max at 300,000."""

from django.db import migrations


def rename_plans(apps, schema_editor):
    Program = apps.get_model("core", "Program")

    # 1. lite → standard (99,000)
    Program.objects.filter(plan="lite").update(plan="standard", monthly_price=99_000)

    # 2. basic → pro (150,000 promo — was 198,000 standard)
    Program.objects.filter(plan="basic").update(plan="pro", monthly_price=150_000)

    # 3. premium → max (300,000)
    Program.objects.filter(plan="premium").update(plan="max", monthly_price=300_000)


def revert_plans(apps, schema_editor):
    Program = apps.get_model("core", "Program")

    Program.objects.filter(plan="standard").update(plan="lite", monthly_price=55_000)
    Program.objects.filter(plan="pro").update(plan="basic", monthly_price=198_000)
    Program.objects.filter(plan="max").update(plan="premium", monthly_price=300_000)


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0016_update_basic_price_198000"),
    ]

    operations = [
        migrations.RunPython(rename_plans, revert_plans),
    ]
