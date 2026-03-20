# Generated manually: Convert integer tenant_id to ForeignKey
# TranscodeJob, OpsEvent, VideoLike, VideoComment

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0001_initial"),
        ("video", "0009_video_deleted_at"),
    ]

    operations = [
        # TranscodeJob: tenant_id (PositiveIntegerField, NOT NULL) → ForeignKey(CASCADE)
        migrations.AlterField(
            model_name="videotranscodejob",
            name="tenant_id",
            field=models.ForeignKey(
                db_column="tenant_id",
                on_delete=django.db.models.deletion.CASCADE,
                related_name="video_transcode_jobs",
                to="core.tenant",
            ),
        ),
        migrations.RenameField(
            model_name="videotranscodejob",
            old_name="tenant_id",
            new_name="tenant",
        ),

        # VideoOpsEvent: tenant_id (PositiveIntegerField, NULL) → ForeignKey(SET_NULL, NULL)
        migrations.AlterField(
            model_name="videoopsevent",
            name="tenant_id",
            field=models.ForeignKey(
                blank=True,
                db_column="tenant_id",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="video_ops_events",
                to="core.tenant",
            ),
        ),
        migrations.RenameField(
            model_name="videoopsevent",
            old_name="tenant_id",
            new_name="tenant",
        ),

        # VideoLike: tenant_id (PositiveIntegerField, NOT NULL) → ForeignKey(CASCADE)
        migrations.AlterField(
            model_name="videolike",
            name="tenant_id",
            field=models.ForeignKey(
                db_column="tenant_id",
                on_delete=django.db.models.deletion.CASCADE,
                related_name="video_likes",
                to="core.tenant",
            ),
        ),
        migrations.RenameField(
            model_name="videolike",
            old_name="tenant_id",
            new_name="tenant",
        ),

        # VideoComment: tenant_id (PositiveIntegerField, NOT NULL) → ForeignKey(CASCADE)
        migrations.AlterField(
            model_name="videocomment",
            name="tenant_id",
            field=models.ForeignKey(
                db_column="tenant_id",
                on_delete=django.db.models.deletion.CASCADE,
                related_name="video_comments",
                to="core.tenant",
            ),
        ),
        migrations.RenameField(
            model_name="videocomment",
            old_name="tenant_id",
            new_name="tenant",
        ),

        # VideoLike index: 기존 인덱스는 DB 컬럼명(tenant_id)을 참조하므로
        # FK 전환 후에도 동일 컬럼이라 그대로 유효. Django state만 업데이트.
        # (DB 인덱스 video_video_tenant__d89242_idx는 유지됨)
    ]
