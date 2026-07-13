import django.db.models.deletion
from django.db import migrations, models


def assert_video_uniqueness_preconditions(apps, schema_editor):
    video_table = schema_editor.quote_name("video_video")
    folder_table = schema_editor.quote_name("video_videofolder")
    checks = (
        (
            video_table,
            "deleted_at IS NULL AND folder_id IS NOT NULL",
            "tenant_id, folder_id, \"order\"",
            "active folder video order duplicates",
        ),
        (
            video_table,
            "deleted_at IS NULL AND folder_id IS NULL AND session_id IS NOT NULL",
            "tenant_id, session_id, \"order\"",
            "active session video order duplicates",
        ),
        (
            folder_table,
            "parent_id IS NULL AND tenant_id IS NOT NULL",
            "tenant_id, name",
            "root video folder name duplicates",
        ),
        (
            folder_table,
            "parent_id IS NOT NULL AND tenant_id IS NOT NULL",
            "tenant_id, parent_id, name",
            "child video folder name duplicates",
        ),
    )
    with schema_editor.connection.cursor() as cursor:
        if schema_editor.connection.vendor == "postgresql":
            cursor.execute(
                f"LOCK TABLE {video_table}, {folder_table} "
                "IN SHARE ROW EXCLUSIVE MODE"
            )
        cursor.execute(
            f"SELECT 1 FROM {folder_table} WHERE tenant_id IS NULL LIMIT 1"
        )
        if cursor.fetchone():
            raise RuntimeError(
                "VideoFolder rows without a tenant remain; repair them before "
                "applying video.0019_video_order_and_folder_uniqueness"
            )
        for table, predicate, group_fields, description in checks:
            cursor.execute(
                f"""
                SELECT 1
                FROM {table}
                WHERE {predicate}
                GROUP BY {group_fields}
                HAVING COUNT(*) > 1
                LIMIT 1
                """
            )
            if cursor.fetchone():
                raise RuntimeError(
                    f"{description} remain; repair them before applying "
                    "video.0019_video_order_and_folder_uniqueness"
                )


class Migration(migrations.Migration):

    dependencies = [
        ("video", "0018_video_source_type_youtube"),
    ]

    operations = [
        migrations.RunPython(
            assert_video_uniqueness_preconditions,
            reverse_code=migrations.RunPython.noop,
        ),
        migrations.AddConstraint(
            model_name="video",
            constraint=models.UniqueConstraint(
                condition=models.Q(deleted_at__isnull=True, folder__isnull=False),
                fields=("tenant", "folder", "order"),
                name="uniq_active_video_order_per_folder",
            ),
        ),
        migrations.AddConstraint(
            model_name="video",
            constraint=models.UniqueConstraint(
                condition=models.Q(
                    deleted_at__isnull=True,
                    folder__isnull=True,
                    session__isnull=False,
                ),
                fields=("tenant", "session", "order"),
                name="uniq_active_video_order_per_session",
            ),
        ),
        migrations.AlterField(
            model_name="videofolder",
            name="tenant",
            field=models.ForeignKey(
                db_index=True,
                help_text="폴더 소유 테넌트",
                on_delete=django.db.models.deletion.CASCADE,
                related_name="video_folders",
                to="core.tenant",
            ),
        ),
        migrations.RemoveConstraint(
            model_name="videofolder",
            name="unique_video_folder_name_per_tenant",
        ),
        migrations.AddConstraint(
            model_name="videofolder",
            constraint=models.UniqueConstraint(
                condition=models.Q(parent__isnull=True),
                fields=("tenant", "name"),
                name="uniq_root_video_folder_name_per_tenant",
            ),
        ),
        migrations.AddConstraint(
            model_name="videofolder",
            constraint=models.UniqueConstraint(
                condition=models.Q(parent__isnull=False),
                fields=("tenant", "parent", "name"),
                name="uniq_child_video_folder_name_per_tenant",
            ),
        ),
    ]
