from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("exams", "0027_exam_student_rank_visibility"),
    ]

    operations = [
        migrations.AddField(
            model_name="sheet",
            name="essay_score_marks",
            field=models.BooleanField(
                db_default=False,
                default=False,
                help_text="서술형 칸마다 교사 점수 마킹칸을 인쇄하고 스캔에서 읽는다(Beta).",
            ),
        ),
        migrations.AddField(
            model_name="sheet",
            name="essay_score_marks_scan_shape",
            field=models.CharField(
                max_length=64,
                db_default="",
                default="",
                help_text="인쇄를 꺼도 기존 서술형 교사 채점란을 판독한다(Beta).",
            ),
        ),
    ]
