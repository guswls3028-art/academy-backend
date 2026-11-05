from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("exams", "0026_exam_essay_numbering"),
    ]

    operations = [
        migrations.AddField(
            model_name="exam",
            name="student_rank_published",
            field=models.BooleanField(
                db_default=True,
                default=True,
                help_text="학생·학부모 성적 화면에 등수를 표시한다.",
            ),
        ),
        migrations.AddField(
            model_name="exam",
            name="student_cohort_size_published",
            field=models.BooleanField(
                db_default=True,
                default=True,
                help_text="등수 산정에 포함된 응시 인원을 학생·학부모 성적 화면에 표시한다.",
            ),
        ),
    ]
