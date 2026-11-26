from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("exams", "0028_sheet_essay_score_marks")]

    operations = [
        # Only the ORM default changes for new Sheet instances. The database
        # default and every existing value stay as they were in 0028.
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.AlterField(
                    model_name="sheet",
                    name="essay_score_marks",
                    field=models.BooleanField(
                        default=True,
                        db_default=False,
                        help_text="서술형 칸마다 교사 점수 마킹칸을 인쇄하고 스캔에서 읽는다(Beta).",
                    ),
                ),
            ],
        ),
    ]
