from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("problem_studio", "0004_problemstudiobetarun"),
    ]

    operations = [
        migrations.AddField(
            model_name="problemstudiobetarun",
            name="checkpoint_key",
            field=models.CharField(blank=True, default="", max_length=512),
        ),
        migrations.AddField(
            model_name="problemstudiobetarun",
            name="completed_question_count",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="problemstudiobetarun",
            name="last_error",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="problemstudiobetarun",
            name="question_count",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="problemstudiobetarun",
            name="request_payload",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="problemstudiobetarun",
            name="result_filename",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="problemstudiobetarun",
            name="result_key",
            field=models.CharField(blank=True, default="", max_length=512),
        ),
        migrations.AddField(
            model_name="problemstudiobetarun",
            name="result_payload",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="problemstudiobetarun",
            name="review_required_count",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="problemstudiobetarun",
            name="solutions_key",
            field=models.CharField(blank=True, default="", max_length=512),
        ),
        migrations.AddField(
            model_name="problemstudiobetarun",
            name="source_archive_key",
            field=models.CharField(blank=True, default="", max_length=512),
        ),
        migrations.AddField(
            model_name="problemstudiobetarun",
            name="source_name",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="problemstudiobetarun",
            name="stage",
            field=models.CharField(
                choices=[
                    ("extract", "문항 분석"),
                    ("solve", "정답·해설 생성"),
                    ("verify", "독립 검산"),
                    ("build", "PDF 생성"),
                    ("done", "완료"),
                ],
                db_index=True,
                default="extract",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="problemstudiobetarun",
            name="verified_question_count",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddIndex(
            model_name="problemstudiobetarun",
            index=models.Index(
                fields=["tenant", "requested_by", "created_at"],
                name="idx_ps_beta_owner_created",
            ),
        ),
    ]
