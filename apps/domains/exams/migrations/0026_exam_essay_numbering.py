from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("exams", "0025_examlecturepolicy"),
    ]

    operations = [
        migrations.AddField(
            model_name="exam",
            name="essay_numbering",
            field=models.CharField(
                choices=[("continuous", "이어서 표시"), ("separate", "서술형 1번부터 표시")],
                default="continuous",
                help_text="서술형 표시 번호 방식. 저장된 문항 번호와 채점·OMR 인식 계약은 변경하지 않는다.",
                max_length=10,
            ),
        ),
    ]
