from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("exams", "0020_exam_student_results_published"),
    ]

    operations = [
        migrations.AlterField(
            model_name="exam",
            name="segmentation_status",
            field=models.CharField(
                choices=[
                    ("none", "원본 없음"),
                    ("processing", "문항 분리 중"),
                    ("review_required", "문항·해설 검수 필요"),
                    ("ready", "문항 분리 완료"),
                    ("failed", "문항 분리 실패"),
                    ("conversion_required", "PDF 변환 필요"),
                ],
                default="none",
                max_length=24,
            ),
        ),
        migrations.AlterField(
            model_name="questionexplanation",
            name="source",
            field=models.CharField(
                choices=[
                    ("ai_extracted", "AI 추출"),
                    ("source_file", "업로드 원본"),
                    ("manual", "수동 입력"),
                ],
                default="manual",
                max_length=20,
            ),
        ),
        migrations.CreateModel(
            name="ExamQuestionProposal",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("position", models.PositiveIntegerField()),
                ("number", models.PositiveIntegerField()),
                ("detected_number", models.PositiveIntegerField(blank=True, null=True)),
                ("page_index", models.PositiveIntegerField(default=0)),
                ("region_meta", models.JSONField(blank=True, default=dict)),
                ("problem_image_key", models.CharField(blank=True, default="", max_length=500)),
                ("explanation_text", models.TextField(blank=True, default="")),
                ("explanation_image_key", models.CharField(blank=True, default="", max_length=500)),
                ("match_confidence", models.FloatField(blank=True, null=True)),
                ("problem_crop_ratio", models.FloatField(default=1.0)),
                ("included", models.BooleanField(default=True)),
                ("source_job_id", models.CharField(blank=True, default="", max_length=64)),
                ("engine", models.CharField(blank=True, default="", max_length=32)),
                ("exam", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="question_proposals", to="exams.exam")),
            ],
            options={
                "db_table": "exams_question_proposal",
                "ordering": ["position", "id"],
            },
        ),
        migrations.AddConstraint(
            model_name="examquestionproposal",
            constraint=models.UniqueConstraint(fields=("exam", "position"), name="exams_question_proposal_exam_position_uniq"),
        ),
    ]
