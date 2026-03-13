from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("exams", "0004_allow_regular_exam_without_template"),
    ]

    operations = [
        migrations.AddField(
            model_name="exam",
            name="max_score",
            field=models.FloatField(
                default=100.0,
                help_text="만점. 답안등록 없이 합산 입력 시 사용. 답안등록 시 문항 합산으로 자동 재계산.",
            ),
        ),
        migrations.AddField(
            model_name="exam",
            name="display_order",
            field=models.PositiveIntegerField(
                default=0,
                help_text="성적탭 내 표시 순서 (작을수록 앞)",
            ),
        ),
    ]
