from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("exams", "0024_examasset_answer_source"),
        ("lectures", "0008_lecture_display_order"),
    ]

    operations = [
        migrations.CreateModel(
            name="ExamLecturePolicy",
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
                (
                    "pass_score",
                    models.FloatField(help_text="이 강의에서 적용할 합격·귀가 기준 점수"),
                ),
                (
                    "exam",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="lecture_policies",
                        to="exams.exam",
                    ),
                ),
                (
                    "lecture",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="exam_policies",
                        to="lectures.lecture",
                    ),
                ),
            ],
            options={
                "db_table": "exams_exam_lecture_policy",
                "constraints": [
                    models.UniqueConstraint(
                        fields=("exam", "lecture"),
                        name="uniq_exam_lecture_policy",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(("pass_score__gte", 0)),
                        name="exam_lecture_pass_score_gte_zero",
                    ),
                ],
            },
        ),
    ]
