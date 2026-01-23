# PATH: apps/domains/homework_results/migrations/0002_homework_and_more.py
# ✅ FIX: RenameIndex 제거 (DB에 해당 인덱스가 없어서 터짐)

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("lectures", "0002_remove_session_exam"),
        ("homework_results", "0001_initial"),
    ]

    operations = [
        # ✅ Homework 테이블 생성만 수행
        migrations.CreateModel(
            name="Homework",
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
                ("title", models.CharField(max_length=255)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("DRAFT", "초안"),
                            ("OPEN", "진행중"),
                            ("CLOSED", "마감"),
                        ],
                        db_index=True,
                        default="DRAFT",
                        max_length=20,
                    ),
                ),
                ("meta", models.JSONField(blank=True, null=True)),
                (
                    "session",
                    models.ForeignKey(
                        db_index=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="homeworks",
                        to="lectures.session",
                    ),
                ),
            ],
            options={
                "ordering": ["-updated_at", "-id"],
            },
        ),

        # ✅ Homework 인덱스만 생성
        migrations.AddIndex(
            model_name="homework",
            index=models.Index(
                fields=["session", "updated_at"],
                name="homework_re_session_2c33a5_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="homework",
            index=models.Index(
                fields=["session", "status"],
                name="homework_re_session_c13723_idx",
            ),
        ),
    ]
