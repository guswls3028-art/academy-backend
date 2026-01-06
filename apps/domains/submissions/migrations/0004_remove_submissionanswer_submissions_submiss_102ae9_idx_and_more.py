# apps/domains/submissions/migrations/0004_submissionanswer_exam_question_v2.py
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("submissions", "0003_remove_submissionanswer_submissions_submiss_102ae9_idx_and_more"),
    ]

    operations = [
        # -------------------------------------------------
        # ✅ v2 핵심: exam_question_id만 추가
        # -------------------------------------------------
        migrations.AddField(
            model_name="submissionanswer",
            name="exam_question_id",
            field=models.PositiveIntegerField(
                null=True,
                help_text="ExamQuestion.id (v2)",
            ),
        ),

        # -------------------------------------------------
        # ⚠️ question_number는 이미 DB에 존재
        # → AddField 하면 DuplicateColumn 발생
        # → 절대 추가하지 말 것
        # -------------------------------------------------

        migrations.AlterField(
            model_name="submissionanswer",
            name="meta",
            field=models.JSONField(default=dict, blank=True),
        ),

        migrations.AddIndex(
            model_name="submissionanswer",
            index=models.Index(
                fields=["exam_question_id"],
                name="submissions_exam_qu_990095_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="submissionanswer",
            index=models.Index(
                fields=["submission", "exam_question_id"],
                name="submissions_submiss_f557d2_idx",
            ),
        ),
    ]
