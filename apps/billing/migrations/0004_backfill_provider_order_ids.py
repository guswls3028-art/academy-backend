"""
기존 Invoice, PaymentTransaction에 빈 provider_order_id가 있으면 UUID로 backfill.
기존 데이터가 없으면 no-op.
"""

import uuid

from django.db import migrations


def backfill_invoice_order_ids(apps, schema_editor):
    Invoice = apps.get_model("billing", "Invoice")
    for inv in Invoice.objects.filter(provider_order_id=""):
        inv.provider_order_id = f"ord_{uuid.uuid4().hex}"
        inv.save(update_fields=["provider_order_id"])


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0003_invoice_and_payment_fields"),
    ]

    operations = [
        migrations.RunPython(backfill_invoice_order_ids, noop),
    ]
