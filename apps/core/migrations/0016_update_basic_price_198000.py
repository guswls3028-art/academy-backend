"""Update Basic plan monthly_price from 150,000 to 198,000."""

from django.db import migrations


def update_basic_price(apps, schema_editor):
    Program = apps.get_model("core", "Program")
    Program.objects.filter(plan="basic").update(monthly_price=198_000)


def revert_basic_price(apps, schema_editor):
    Program = apps.get_model("core", "Program")
    Program.objects.filter(plan="basic").update(monthly_price=150_000)


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0015_set_subscription_for_all_tenants"),
    ]

    operations = [
        migrations.RunPython(update_basic_price, revert_basic_price),
    ]
