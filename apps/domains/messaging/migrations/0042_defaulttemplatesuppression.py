from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("messaging", "0041_alimtalkchannelbinding_alimtalktemplatebinding"),
    ]

    operations = [
        migrations.CreateModel(
            name="DefaultTemplateSuppression",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("default_key", models.CharField(max_length=64)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("tenant", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="suppressed_message_defaults", to="core.tenant")),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(fields=("tenant", "default_key"), name="uniq_suppressed_message_default"),
                ],
            },
        ),
    ]
