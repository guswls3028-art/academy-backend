from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0056_alter_tenant_messaging_provider"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="opsauditlog",
            index=models.Index(
                fields=["target_user", "-created_at"],
                name="ops_audit_l_target_idx",
            ),
        ),
    ]
