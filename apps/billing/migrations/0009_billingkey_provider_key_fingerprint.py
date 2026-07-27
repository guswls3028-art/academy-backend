from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("billing", "0008_encrypt_billing_key_storage"),
    ]

    operations = [
        migrations.AddField(
            model_name="billingkey",
            name="provider_key_fingerprint",
            field=models.CharField(
                blank=True,
                db_index=True,
                help_text="PG 빌링키 SHA-256 식별자 (삭제 웹훅 조회용)",
                max_length=64,
            ),
        ),
    ]
