import django.db.models.deletion
from django.db import migrations, models


def null_invalid_source_tenant_ids(apps, schema_editor):
    NotificationLog = apps.get_model("messaging", "NotificationLog")
    Tenant = apps.get_model("core", "Tenant")
    db_alias = schema_editor.connection.alias

    valid_tenant_ids = Tenant.objects.using(db_alias).values("id")
    (
        NotificationLog.objects.using(db_alias)
        .exclude(source_tenant_id__isnull=True)
        .exclude(source_tenant_id__in=valid_tenant_ids)
        .update(source_tenant_id=None)
    )


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0040_pending_password_reset"),
        ("messaging", "0031_disable_unready_autosend_configs"),
    ]

    operations = [
        migrations.RunPython(null_invalid_source_tenant_ids, noop_reverse),
        migrations.AlterField(
            model_name="notificationlog",
            name="source_tenant_id",
            field=models.ForeignKey(
                blank=True,
                db_column="source_tenant_id",
                db_index=True,
                help_text="오너 대리발송의 원 소속 테넌트",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="source_notification_logs",
                to="core.tenant",
            ),
        ),
        migrations.RenameField(
            model_name="notificationlog",
            old_name="source_tenant_id",
            new_name="source_tenant",
        ),
    ]
