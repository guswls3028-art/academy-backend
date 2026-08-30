from django.db import migrations, models
import django.db.models.deletion


def backfill_scope(apps, schema_editor):
    Item = apps.get_model("submissions", "OmrUploadBatchItem")
    for item in Item.objects.select_related("batch").iterator(chunk_size=500):
        item.tenant_id = item.batch.tenant_id
        item.exam_id = item.batch.exam_id
        item.save(update_fields=["tenant_id", "exam_id"])


class Migration(migrations.Migration):
    dependencies = [("submissions", "0010_omruploadbatch_omruploadbatchitem")]

    operations = [
        migrations.AddField(
            model_name="omruploadbatchitem",
            name="tenant",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="omr_upload_batch_items",
                to="core.tenant",
            ),
        ),
        migrations.AddField(
            model_name="omruploadbatchitem",
            name="exam_id",
            field=models.PositiveBigIntegerField(null=True),
        ),
        migrations.AddField(
            model_name="omruploadbatchitem",
            name="duplicate_of_submission",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="duplicate_omr_upload_items",
                to="submissions.submission",
            ),
        ),
        migrations.AddField(
            model_name="omruploadbatchitem",
            name="content_sha256",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.AlterField(
            model_name="omruploadbatchitem",
            name="admission_status",
            field=models.CharField(
                choices=[
                    ("pending", "Pending"),
                    ("received", "Received"),
                    ("duplicate", "Duplicate"),
                    ("failed", "Failed"),
                ],
                default="pending",
                max_length=16,
            ),
        ),
        migrations.RunPython(backfill_scope, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="omruploadbatchitem",
            name="tenant",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="omr_upload_batch_items",
                to="core.tenant",
            ),
        ),
        migrations.AlterField(
            model_name="omruploadbatchitem",
            name="exam_id",
            field=models.PositiveBigIntegerField(),
        ),
        migrations.AddIndex(
            model_name="omruploadbatchitem",
            index=models.Index(
                fields=["tenant", "exam_id", "content_sha256"],
                name="omr_item_exam_hash_idx",
            ),
        ),
        migrations.AddConstraint(
            model_name="omruploadbatchitem",
            constraint=models.UniqueConstraint(
                condition=(
                    models.Q(admission_status="received")
                    & ~models.Q(content_sha256="")
                ),
                fields=("tenant", "exam_id", "content_sha256"),
                name="uniq_received_omr_exam_content",
            ),
        ),
    ]
