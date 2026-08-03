from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("exams", "0021_exam_question_segmentation_review"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.AlterField(
                    model_name="examasset",
                    name="asset_type",
                    field=models.CharField(
                        choices=[
                            ("problem_pdf", "Problem PDF"),
                            ("problem_source", "Original problem source"),
                            (
                                "teacher_explanation_source",
                                "Teacher explanation source",
                            ),
                            ("omr_sheet", "OMR Sheet"),
                        ],
                        max_length=30,
                    ),
                ),
            ],
        ),
    ]
