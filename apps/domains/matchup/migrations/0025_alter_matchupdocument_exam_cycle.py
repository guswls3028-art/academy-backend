from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("matchup", "0024_artifact_scan_intent"),
    ]

    operations = [
        migrations.AlterField(
            model_name="matchupdocument",
            name="exam_cycle",
            field=models.CharField(
                blank=True,
                choices=[
                    ("", "미지정"),
                    ("semester1_midterm", "1학기 중간고사"),
                    ("semester1_final", "1학기 기말고사"),
                    ("semester2_midterm", "2학기 중간고사"),
                    ("semester2_final", "2학기 기말고사"),
                    ("midterm", "중간고사 (학기 미지정)"),
                    ("final", "기말고사 (학기 미지정)"),
                    ("mock", "모의고사"),
                    ("other", "기타"),
                ],
                db_index=True,
                default="",
                help_text="시험 회차 분류 (랜딩 학교별 grouping)",
                max_length=20,
            ),
        ),
    ]
