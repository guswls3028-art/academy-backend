from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("exams", "0023_alter_examasset_file_type")]

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
                            ("answer_source", "Original answer-key source"),
                            ("omr_sheet", "OMR Sheet"),
                        ],
                        max_length=30,
                    ),
                ),
            ],
        ),
    ]
