"""Set ymath/limglish contract monthly prices to 150,000."""

from django.db import migrations


CONTRACT_TENANT_CODES = ("limglish", "ymath")
CONTRACT_MONTHLY_PRICE = 150_000


def apply_contract_prices(apps, schema_editor):
    Program = apps.get_model("core", "Program")

    Program.objects.filter(
        tenant__code__in=CONTRACT_TENANT_CODES,
    ).update(monthly_price=CONTRACT_MONTHLY_PRICE)


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0040_pending_password_reset"),
    ]

    operations = [
        migrations.RunPython(apply_contract_prices, migrations.RunPython.noop),
    ]
