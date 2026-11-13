from django.db import migrations, models


ACADEMY_MIGRATION_PHASE = "contract"
ACADEMY_MIGRATION_REASON = (
    "Widen the existing inbox kind column for the new school report request; "
    "existing values and writes stay valid."
)


def limit_ddl_wait(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        with schema_editor.connection.cursor() as cursor:
            cursor.execute("SET LOCAL lock_timeout = '5s'")
            cursor.execute("SET LOCAL statement_timeout = '30s'")


class Migration(migrations.Migration):
    dependencies = [("community", "0024_postreplyattachment")]

    operations = [
        migrations.RunPython(limit_ddl_wait, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="communitynotification",
            name="kind",
            field=models.CharField(
                choices=[
                    ("post_reply", "내 글에 댓글"),
                    ("post_like", "내 글에 좋아요"),
                    ("reply_like", "내 댓글에 좋아요"),
                    ("child_reply", "내 댓글에 답글"),
                    ("school_report_request", "학교 성적 제출 요청"),
                ],
                db_index=True,
                max_length=24,
            ),
        ),
        migrations.RunPython(migrations.RunPython.noop, limit_ddl_wait),
    ]
