# Generated manually — QuestionExplanation model

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("exams", "0009_backfill_exam_tenant"),
    ]

    operations = [
        migrations.CreateModel(
            name="QuestionExplanation",
            fields=[
                (
                    "id",
                    models.AutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "text",
                    models.TextField(
                        blank=True,
                        default="",
                        help_text="해설 텍스트 (AI 추출 또는 강사 입력)",
                    ),
                ),
                (
                    "image_key",
                    models.CharField(
                        blank=True,
                        default="",
                        help_text="R2에 저장된 해설 이미지 키",
                        max_length=500,
                    ),
                ),
                (
                    "source",
                    models.CharField(
                        choices=[
                            ("ai_extracted", "AI 추출"),
                            ("manual", "수동 입력"),
                        ],
                        default="manual",
                        max_length=20,
                    ),
                ),
                (
                    "match_confidence",
                    models.FloatField(
                        blank=True,
                        help_text="AI 문항-해설 매칭 신뢰도",
                        null=True,
                    ),
                ),
                (
                    "question",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="explanation",
                        to="exams.examquestion",
                    ),
                ),
            ],
            options={
                "db_table": "exams_question_explanation",
                "verbose_name": "문항 해설",
            },
        ),
    ]
