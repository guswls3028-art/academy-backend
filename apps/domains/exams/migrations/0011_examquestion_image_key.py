# Generated migration — add image_key to ExamQuestion

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("exams", "0010_add_question_explanation"),
    ]

    operations = [
        migrations.AddField(
            model_name="examquestion",
            name="image_key",
            field=models.CharField(
                blank=True,
                default="",
                help_text="R2에 저장된 문항 크롭 이미지 키",
                max_length=500,
            ),
        ),
    ]
