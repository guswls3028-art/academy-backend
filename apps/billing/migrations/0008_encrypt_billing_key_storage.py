from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("billing", "0007_paymenttransaction_processing_state"),
    ]

    operations = [
        migrations.AlterField(
            model_name="billingkey",
            name="billing_key",
            field=models.CharField(
                help_text="PG사 빌링키 (애플리케이션 암호화 저장)",
                max_length=512,
            ),
        ),
    ]
