from django.db import migrations, models


SINGLE_PLAN = "all"
MONTHLY_SUPPLY_AMOUNT = 145_000
MONTHLY_TAX_AMOUNT = 14_000
MONTHLY_TOTAL_AMOUNT = 159_000


def apply_single_subscription_plan(apps, schema_editor):
    Program = apps.get_model("core", "Program")
    Invoice = apps.get_model("billing", "Invoice")

    Program.objects.all().update(
        plan=SINGLE_PLAN,
        monthly_price=MONTHLY_SUPPLY_AMOUNT,
    )
    # SCHEDULED rows have not been issued or charged yet, so converge them to
    # the new contract. Issued PENDING/FAILED/OVERDUE rows remain snapshots.
    Invoice.objects.filter(status="SCHEDULED").update(
        plan=SINGLE_PLAN,
        supply_amount=MONTHLY_SUPPLY_AMOUNT,
        tax_amount=MONTHLY_TAX_AMOUNT,
        total_amount=MONTHLY_TOTAL_AMOUNT,
    )


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0008_encrypt_billing_key_storage"),
        ("core", "0045_unify_subscription_plan"),
    ]

    operations = [
        migrations.RunPython(apply_single_subscription_plan),
        migrations.AlterField(
            model_name="program",
            name="plan",
            field=models.CharField(
                choices=[("all", "전체 기능")],
                default="all",
                help_text="단일 전체 기능 요금제",
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name="program",
            name="monthly_price",
            field=models.PositiveIntegerField(
                default=145000,
                help_text="월 공급가액(원). 단일 요금제 기준 145,000원.",
            ),
        ),
    ]
