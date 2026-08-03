from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("exams", "0019_exam_grading_workflow"),
    ]

    operations = [
        migrations.AddField(
            model_name="exam",
            name="student_results_published",
            field=models.BooleanField(
                default=True,
                help_text=(
                    "학생·학부모 성적 공개 여부. 비공개여도 교직원 채점·통계 기록은 유지한다."
                ),
            ),
        ),
    ]
