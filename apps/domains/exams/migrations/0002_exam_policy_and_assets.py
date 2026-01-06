# apps/domains/exams/migrations/0002_exam_policy_and_assets.py
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("exams", "0001_initial"),
    ]

    operations = [
        # ==============================
        # ✅ Exam 정책 필드 추가
        # ==============================
        migrations.AddField(
            model_name="exam",
            name="allow_retake",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="exam",
            name="max_attempts",
            field=models.PositiveIntegerField(default=1),
        ),
        migrations.AddField(
            model_name="exam",
            name="pass_score",
            field=models.FloatField(default=0.0),
        ),
        migrations.AddField(
            model_name="exam",
            name="open_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="exam",
            name="close_at",
            field=models.DateTimeField(blank=True, null=True),
        ),

        # ==============================
        # ✅ ExamAsset 신규 테이블
        # ==============================
        migrations.CreateModel(
            name="ExamAsset",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),

                ("asset_type", models.CharField(max_length=30)),
                ("file_key", models.CharField(max_length=512)),
                ("file_type", models.CharField(blank=True, max_length=50, null=True)),
                ("file_size", models.PositiveIntegerField(blank=True, null=True)),
                (
                    "exam",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="assets",
                        to="exams.exam",
                    ),
                ),
            ],
            options={
                "db_table": "exams_exam_asset",
            },
        ),
        migrations.AddIndex(
            model_name="examasset",
            index=models.Index(fields=["exam", "asset_type"], name="exams_examasset_exam_asset_type_idx"),
        ),
        migrations.AlterUniqueTogether(
            name="examasset",
            unique_together={("exam", "asset_type")},
        ),
    ]
