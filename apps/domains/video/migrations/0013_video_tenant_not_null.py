"""
Enforce Video.tenant NOT NULL.

All existing videos already have tenant set (verified: 0 null rows).
The field was added nullable in 0011 and backfilled. Now enforce NOT NULL.
"""
from django.db import migrations, models
import django.db.models.deletion


def verify_no_null_tenant(apps, schema_editor):
    Video = apps.get_model("video", "Video")
    null_count = Video.objects.filter(tenant__isnull=True).count()
    if null_count > 0:
        # Backfill from session.lecture.tenant
        for v in Video.objects.filter(tenant__isnull=True).select_related("session__lecture"):
            if v.session and v.session.lecture_id:
                from django.db import connection
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT tenant_id FROM lectures_lecture WHERE id = %s",
                        [v.session.lecture_id],
                    )
                    row = cursor.fetchone()
                    if row and row[0]:
                        v.tenant_id = row[0]
                        v.save(update_fields=["tenant_id"])

        # Remaining orphans -> tenant 1
        still_null = Video.objects.filter(tenant__isnull=True).count()
        if still_null > 0:
            Tenant = apps.get_model("core", "Tenant")
            if Tenant.objects.filter(id=1).exists():
                Video.objects.filter(tenant__isnull=True).update(tenant_id=1)
            else:
                raise Exception(f"{still_null} videos have no tenant and Tenant 1 doesn't exist")


class Migration(migrations.Migration):

    dependencies = [
        ("video", "0012_alter_video_options_and_more"),
        ("core", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(verify_no_null_tenant, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="video",
            name="tenant",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="videos",
                to="core.tenant",
                db_index=True,
                help_text="영상 소유 테넌트 (SSOT, session→lecture→tenant 체인 대체)",
            ),
        ),
    ]
