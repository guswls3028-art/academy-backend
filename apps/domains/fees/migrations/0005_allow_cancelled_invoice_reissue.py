from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("fees", "0004_add_idempotency_key_to_fee_payment"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="studentinvoice",
            name="uniq_student_invoice_per_period",
        ),
        migrations.AddConstraint(
            model_name="studentinvoice",
            constraint=models.UniqueConstraint(
                condition=~models.Q(status="CANCELLED"),
                fields=("tenant", "student", "billing_year", "billing_month"),
                name="uniq_active_student_invoice_per_period",
            ),
        ),
    ]
